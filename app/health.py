"""
Healthcheck global — vérifie PostgreSQL, Neo4j et Redis
"""

from app.db.postgres import pg_pool
from app.db.neo4j import neo4j_driver
from app.db.redis import redis_client


async def check_all_services() -> dict:
    """
    Retourne le statut de chaque service.
    Status global = 'healthy' seulement si les 3 sont OK.
    """
    pg_ok = await pg_pool.is_healthy()
    neo4j_ok = neo4j_driver.is_healthy()
    redis_ok = await redis_client.is_healthy()

    all_ok = pg_ok and neo4j_ok and redis_ok

    return {
        "status": "healthy" if all_ok else "degraded",
        "services": {
            "postgresql": "up" if pg_ok else "down",
            "neo4j": "up" if neo4j_ok else "down",
            "redis": "up" if redis_ok else "down",
        },
    }
