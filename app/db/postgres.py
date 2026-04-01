"""
Connexion PostgreSQL — pool async via asyncpg
"""

import asyncpg
from app.config import settings


class PostgresPool:
    """Wrapper autour du pool asyncpg."""

    def __init__(self):
        self._pool: asyncpg.Pool | None = None

    async def connect(self):
        # asyncpg attend une URL sans le driver SQLAlchemy
        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        self._pool = await asyncpg.create_pool(dsn=dsn, min_size=2, max_size=10)

    async def disconnect(self):
        if self._pool:
            await self._pool.close()

    async def is_healthy(self) -> bool:
        """Vérifie que PostgreSQL répond."""
        try:
            async with self._pool.acquire() as conn:
                result = await conn.fetchval("SELECT 1")
                return result == 1
        except Exception:
            return False

    @property
    def pool(self) -> asyncpg.Pool:
        if not self._pool:
            raise RuntimeError("PostgreSQL pool non initialisé. Appeler connect() d'abord.")
        return self._pool


pg_pool = PostgresPool()
