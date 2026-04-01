"""
AI-Powered Data Analyzer — API Gateway (Layer 2)
Point d'entrée FastAPI avec healthcheck de tous les services.
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

    # Startup
    await pg_pool.connect()
    neo4j_driver.verify_connectivity()
    await redis_client.ping()
    logger.info("Tous les services sont connectés.")

    # Initialisation du Knowledge Graph (Ticket 2)
    kg_result = neo4j_driver.init_knowledge_graph()
    logger.info(
        "Knowledge Graph initialisé : %d contraintes, %d index.",
        len(kg_result["constraints_created"]),
        len(kg_result["indexes_created"]),
    )

    yield

    # Shutdown
    await pg_pool.disconnect()
    neo4j_driver.close()
    await redis_client.close()
    logger.info("Connexions fermées.")


# ─── Application FastAPI ──────────────────────────────────────
app = FastAPI(
    title="AI-Powered Data Analyzer",
    description="Agent d'analyse augmentée par IA",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # À restreindre en production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Routes ───────────────────────────────────────────────────
@app.get("/")
async def root():
    return {"message": "AI-Powered Data Analyzer — API Gateway"}


@app.get("/health")
async def health():
    """Healthcheck global : vérifie PostgreSQL, Neo4j et Redis."""
    status = await check_all_services()
    http_status = 200 if status["status"] == "healthy" else 503
    return JSONResponse(content=status, status_code=http_status)


@app.get("/neo4j/status")
async def neo4j_status():
    """
    Retourne l'état du Knowledge Graph :
    contraintes, index et nœuds par label.
    """
    from app.db.neo4j import neo4j_driver
    try:
        schema = neo4j_driver.get_schema_status()
        return {"status": "ready", "schema": schema}
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "detail": str(e)},
            status_code=503,
        )


@app.get("/redis/status")
async def redis_status():
    """
    Retourne l'état du cache Redis :
    clés par namespace, mémoire utilisée.
    """
    from app.db.redis import redis_client
    try:
        cache_info = await redis_client.get_cache_info()
        return {"status": "ready", "cache": cache_info}
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "detail": str(e)},
            status_code=503,
        )


@app.get("/redis/stats")
async def redis_stats():
    """
    Retourne les statistiques du cache :
    hits, misses, hit rates, rate limits, etc.
    """
    from app.db.redis import redis_client
    try:
        stats = await redis_client.get_stats()
        return {"status": "ok", "stats": stats}
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "detail": str(e)},
            status_code=503,
        )