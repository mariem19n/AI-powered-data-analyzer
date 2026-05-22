"""
AI-Powered Data Analyzer — API Gateway (Layer 2)
Point d'entrée FastAPI avec healthcheck de tous les services.

Sprint 2 : intègre l'Orchestrator LangGraph avec le pipeline
Semantic Layer du Sprint 1.
"""

import asyncio
import logging
from datetime import date, datetime
from decimal import Decimal
from contextlib import asynccontextmanager

import psycopg2
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.health import check_all_services
from app.orchestrator.conversation_context import ConversationContextResolver

from app.api.routes.feedback import router as feedback_router

logger = logging.getLogger(__name__)

MAIN_CRYPTO_SYMBOLS = ("BTC", "ETH", "BNB", "XRP", "ADA", "SOL", "DOT", "DOGE", "AVAX", "LINK")


def _to_json_value(value):
    """Convertit les types PostgreSQL non JSON en valeurs simples."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


# ─── Helpers de démarrage résilient ──────────────────────────


async def _wait_for(
    name: str,
    probe,
    *,
    retries: int = 30,
    delay: float = 2.0,
    is_async: bool = True,
):
    """
    Retry une fonction de connectivité jusqu'à ce qu'elle réussisse.

    Pourquoi : même avec les healthchecks Docker bien configurés,
    il peut y avoir une fenêtre de quelques secondes entre
    "service marqué healthy" et "service réellement prêt à
    accepter une connexion applicative" (ex : Neo4j Bolt vs HTTP,
    pool Postgres pas encore rempli, etc). Plutôt que de crasher
    l'API et entrer en boucle de restart, on retente proprement.
    """
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            if is_async:
                await probe()
            else:
                await asyncio.to_thread(probe)
            logger.info("%s : connecté (tentative %d).", name, attempt)
            return
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning(
                "%s : indisponible (tentative %d/%d) — %s",
                name, attempt, retries, e.__class__.__name__,
            )
            await asyncio.sleep(delay)
    logger.error("%s : impossible de se connecter après %d tentatives.", name, retries)
    raise RuntimeError(f"{name} unreachable") from last_err


# ─── Lifespan : connexion/déconnexion aux services ───────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise les connexions au démarrage, les ferme à l'arrêt."""
    from app.db.postgres import pg_pool
    from app.db.neo4j import neo4j_driver
    from app.db.redis import redis_client

    # ── 1. Connexion aux services (avec retry) ────────────────
    await _wait_for("PostgreSQL", pg_pool.connect, is_async=True)
    await _wait_for(
        "Neo4j", neo4j_driver.verify_connectivity, is_async=False
    )
    await _wait_for("Redis", redis_client.ping, is_async=True)
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

    # SQL Agent — le vrai, avec template hybride + LLM + sécurité
    from app.agents.sql_agent import SQLAgent
    from app.agents.sql_executor import DirectSQLExecutor

    # Extraire host/port depuis database_url de la config
    # Format: postgresql+asyncpg://user:pass@host:port/dbname
    from urllib.parse import urlparse

    db_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    parsed = urlparse(db_url)

    sql_executor = DirectSQLExecutor(
        host=parsed.hostname or "postgres",
        port=parsed.port or 5432,
        dbname=parsed.path.lstrip("/") if parsed.path else "analyzer_db",
        user=parsed.username or "analyzer",
        password=parsed.password or "analyzer_pg_123",
    )
    sql_agent = SQLAgent(
        executor=sql_executor,
        redis_client=redis_client,
    )
    logger.info("SQLAgent initialisé avec DirectSQLExecutor.")

    # Factory pour ouvrir une connexion read-only PostgreSQL.
    # Utilisée par l'AnalysisAgent pour l'enrichissement GDELT
    # (lecture des articles publiés aux dates d'anomalies).
    # On utilise psycopg2 directement (pas asyncpg) car la task
    # est synchrone et n'a pas besoin d'un pool partagé.
    def get_pg_readonly_connection():
        return psycopg2.connect(
            host=parsed.hostname or "postgres",
            port=parsed.port or 5432,
            dbname=parsed.path.lstrip("/") if parsed.path else "analyzer_db",
            user=parsed.username or "analyzer",
            password=parsed.password or "analyzer_pg_123",
        )

    # Analyse Agent — vrai runner Sprint 3, adapté au contrat async du PlanExecutor
    from app.agents.analysis import AnalysisAgent

    class AsyncAnalysisAgentAdapter(AgentRunner):
        def __init__(self, analysis_agent: AnalysisAgent):
            self._analysis_agent = analysis_agent

        async def run(self, instruction, upstream_results):
            response = await asyncio.to_thread(
                self._analysis_agent.run,
                instruction=instruction,
                upstream_results=upstream_results,
                semantic_context=instruction.get("semantic_context", {}),
            )
            return response.to_dict() if hasattr(response, "to_dict") else response

    analysis_agent = AsyncAnalysisAgentAdapter(
        AnalysisAgent.build_default(
            neo4j_driver=neo4j_driver,
            db_session_factory=get_pg_readonly_connection,
        )
    )
    logger.info(
        "AnalysisAgent initialisé et branché sur l'Orchestrator "
        "(enrichissement GDELT activé)."
    )

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
    
    context_resolver = ConversationContextResolver(
        redis_client=redis_client,
        llm_client=None,  # Règles-first, LLM optionnel — à brancher si besoin
        max_turns=3,
        ttl_seconds=1800,  # 30 minutes
    )
    logger.info("ConversationContextResolver initialisé (max_turns=3, TTL=30min).")

    # Setup de l'Orchestrator
    orchestrator = setup_orchestrator(
        agents={
            AgentType.SQL_AGENT: sql_agent,
            AgentType.ANALYSIS_AGENT: analysis_agent,
        },
        semantic_build_fn=semantic_pipeline.run,
        external_tool=external_tool,
        context_resolver=context_resolver,
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
app.include_router(feedback_router)


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


@app.get("/api/crypto/latest-prices")
async def latest_crypto_prices():
    """Retourne les derniers prix disponibles pour les principales cryptos."""
    from app.db.postgres import pg_pool

    query = """
        WITH ranked_prices AS (
            SELECT
                f.symbol,
                COALESCE(dc.name, f.symbol) AS name,
                f.date,
                f.close_usd AS price_usd,
                f.volume,
                LAG(f.close_usd) OVER (
                    PARTITION BY f.symbol
                    ORDER BY f.date
                ) AS prev_close_usd,
                ROW_NUMBER() OVER (
                    PARTITION BY f.symbol
                    ORDER BY f.date DESC
                ) AS rn
            FROM fact_crypto_daily f
            LEFT JOIN dim_crypto dc ON dc.symbol = f.symbol
            WHERE f.symbol = ANY($1::text[])
        )
        SELECT
            symbol,
            name,
            price_usd,
            volume,
            date,
            CASE
                WHEN prev_close_usd IS NULL OR prev_close_usd = 0 THEN NULL
                ELSE ROUND(((price_usd - prev_close_usd) / ABS(prev_close_usd) * 100)::numeric, 4)
            END AS change_24h_pct
        FROM ranked_prices
        WHERE rn = 1
        ORDER BY array_position($1::text[], symbol);
    """

    async with pg_pool.pool.acquire() as conn:
        rows = await conn.fetch(query, list(MAIN_CRYPTO_SYMBOLS))

    return [
        {
            "symbol": row["symbol"],
            "name": row["name"],
            "price_usd": _to_json_value(row["price_usd"]),
            "volume": _to_json_value(row["volume"]),
            "date": _to_json_value(row["date"]),
            "change_24h_pct": _to_json_value(row["change_24h_pct"]),
        }
        for row in rows
    ]


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
