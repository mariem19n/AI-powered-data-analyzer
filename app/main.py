"""
AI-Powered Data Analyzer — API Gateway (Layer 2)
Point d'entrée FastAPI avec healthcheck de tous les services.

Sprint 2 : intègre l'Orchestrator LangGraph avec le pipeline
Semantic Layer du Sprint 1.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.health import check_all_services

logger = logging.getLogger(__name__)


# ─── Lifespan : connexion/déconnexion aux services ───────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise les connexions au démarrage, les ferme à l'arrêt."""
    from app.db.postgres import pg_pool
    from app.db.neo4j import neo4j_driver
    from app.db.redis import redis_client

    # ── 1. Connexion aux services ─────────────────────────────
    await pg_pool.connect()
    neo4j_driver.verify_connectivity()
    await redis_client.ping()
    logger.info("Tous les services sont connectés.")

    # ── 2. Initialisation du Knowledge Graph ──────────────────
    kg_result = neo4j_driver.init_knowledge_graph()
    logger.info(
        "Knowledge Graph initialisé : %d contraintes agentic.",
        len(kg_result.get("agentic_constraints", [])),
    )

    # ── 3. Initialisation du Semantic Pipeline ────────────────
    from app.semantic.pipeline import SemanticPipeline

    semantic_pipeline = SemanticPipeline(neo4j_driver)
    app.state.semantic_pipeline = semantic_pipeline

    # ── 4. Initialisation de l'Orchestrator ───────────────────
    from app.orchestrator.schemas import AgentType
    from app.orchestrator.graph import setup_orchestrator
    from app.orchestrator.executor import AgentRunner

    # Agents placeholder — à remplacer par les vrais agents
    # quand le SQL Agent et l'Analyse Agent seront implémentés.
    class PlaceholderSQLAgent(AgentRunner):
        async def run(self, instruction, upstream_results):
            logger.warning(
                "PlaceholderSQLAgent appelé — le vrai SQL Agent "
                "n'est pas encore implémenté."
            )
            return {
                "records": [],
                "columns": [],
                "row_count": 0,
                "sql": "-- placeholder",
            }

    class PlaceholderAnalysisAgent(AgentRunner):
        async def run(self, instruction, upstream_results):
            logger.warning(
                "PlaceholderAnalysisAgent appelé — le vrai Analyse Agent "
                "n'est pas encore implémenté."
            )
            return {
                "insights": ["Analyse non disponible (placeholder)"],
                "visualizations": [],
                "recommendations": [],
            }

    # Fonctions Redis pour le cache de l'Orchestrator
    async def cache_check(question_hash: str):
        """Lookup dans le cache Redis (niveau question)."""
        return await redis_client.get_response_cache(question_hash)

    async def cache_store(question_hash: str, response: dict, ttl: int):
        """Stockage dans le cache Redis."""
        await redis_client.set_response_cache(question_hash, response, ttl)

    # Setup du Tavily External Tool
    from app.orchestrator.external_tool import TavilyExternalTool

    external_tool = TavilyExternalTool()
    if external_tool.is_configured:
        logger.info("TavilyExternalTool configuré et prêt.")
    else:
        logger.warning(
            "TAVILY_API_KEY manquant — les questions hors périmètre "
            "ne pourront pas être traitées via recherche web."
        )

    # Setup de l'Orchestrator
    orchestrator = setup_orchestrator(
        agents={
            AgentType.SQL_AGENT: PlaceholderSQLAgent(),
            AgentType.ANALYSIS_AGENT: PlaceholderAnalysisAgent(),
        },
        semantic_build_fn=semantic_pipeline.run,
        external_tool=external_tool,
        cache_check_fn=cache_check,
        cache_store_fn=cache_store,
        # KG plan lookup/store — sera branché quand le Feedback Agent
        # sera implémenté (Sprint 4). Pour l'instant, pas de réutilisation.
        kg_plan_lookup_fn=None,
        kg_plan_store_fn=None,
        confidence_threshold=0.55,
        cache_ttl_s=settings.cache_ttl_transactional,
    )
    app.state.orchestrator = orchestrator
    logger.info("Orchestrator LangGraph initialisé et prêt.")

    yield

    # ── Shutdown ──────────────────────────────────────────────
    await pg_pool.disconnect()
    neo4j_driver.close()
    await redis_client.close()
    logger.info("Connexions fermées.")


# ─── Application FastAPI ──────────────────────────────────────

app = FastAPI(
    title="AI-Powered Data Analyzer",
    description="Agent d'analyse augmentée par IA",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # À restreindre en production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Routes ───────────────────────────────────────────────────


@app.get("/")
async def root():
    return {"message": "AI-Powered Data Analyzer — API Gateway v0.2"}


@app.get("/health")
async def health():
    """Healthcheck global : vérifie PostgreSQL, Neo4j et Redis."""
    status = await check_all_services()
    http_status = 200 if status["status"] == "healthy" else 503
    return JSONResponse(content=status, status_code=http_status)


@app.get("/neo4j/status")
async def neo4j_status():
    """Retourne l'état du Knowledge Graph."""
    from app.db.neo4j import neo4j_driver

    try:
        schema = neo4j_driver.get_schema_status()
        return {"status": "ready", "schema": schema}
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "detail": str(e)},
            status_code=503,
        )


# ─── Route Orchestrator ──────────────────────────────────────


@app.post("/ask")
async def ask_question(payload: dict):
    """
    Point d'entrée principal — traite une question en langage naturel.

    Body JSON :
        {
            "question": "prix Bitcoin ce mois",
            "session_id": "optional-uuid"
        }

    Retourne un OrchestratorResponse JSON.
    """
    question = payload.get("question", "").strip()
    session_id = payload.get("session_id")

    if not question:
        return JSONResponse(
            content={"error": "Le champ 'question' est vide."},
            status_code=400,
        )

    orchestrator = app.state.orchestrator
    response = await orchestrator.handle(
        question=question,
        session_id=session_id,
    )
    return response.model_dump(mode="json")