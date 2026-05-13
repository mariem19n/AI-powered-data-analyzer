"""
app/agents/sql_executor.py
Interface d'exécution SQL + implémentation directe psycopg2.

Architecture :
  SQLExecutor (Protocol) — interface abstraite
  DirectSQLExecutor — exécution directe via psycopg2 (read-only)
  SandboxSQLExecutor — exécution dans Docker éphémère (futur)

Le SQL Agent utilise l'interface SQLExecutor. On peut changer
l'implémentation sans toucher au SQL Agent.

Sécurité :
  - DirectSQLExecutor utilise une connexion read-only
  - Le SQLSecurityChecker est appliqué AVANT l'exécution
  - Timeout configurable par requête
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ─── Résultat d'exécution ─────────────────────────────────────


@dataclass
class ExecutionResult:
    """Résultat de l'exécution d'une requête SQL."""

    success: bool
    records: list[dict[str, Any]] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    row_count: int = 0
    error: str = ""
    duration_s: float = 0.0

    @staticmethod
    def failed(error: str, duration_s: float = 0.0) -> ExecutionResult:
        return ExecutionResult(
            success=False,
            error=error,
            duration_s=duration_s,
        )


# ─── Interface ────────────────────────────────────────────────


class SQLExecutor(Protocol):
    """
    Interface d'exécution SQL.

    Implémentations :
      - DirectSQLExecutor : psycopg2 direct (Sprint 2)
      - SandboxSQLExecutor : Docker éphémère (futur)
    """

    async def execute(self, sql: str, timeout_s: float = 30.0) -> ExecutionResult:
        """
        Exécute une requête SQL en lecture seule.

        Args:
            sql: requête SELECT validée par le SecurityChecker
            timeout_s: timeout en secondes

        Returns:
            ExecutionResult avec les records ou l'erreur
        """
        ...

    async def is_healthy(self) -> bool:
        """Vérifie la connexion."""
        ...


# ─── Implémentation directe psycopg2 ─────────────────────────


class DirectSQLExecutor:
    """
    Exécution SQL directe via psycopg2.

    Utilise une connexion dédiée en mode read-only :
      - default_transaction_read_only = True
      - statement_timeout configurable
      - Pas de connexion persistante — une nouvelle connexion par requête
        pour éviter les fuites de ressources
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        dbname: str | None = None,
        user: str | None = None,
        password: str | None = None,
    ):
        # Docker : POSTGRES_HOST=postgres, POSTGRES_PORT=5432
        # Local  : DB_HOST=localhost, DB_PORT=5433
        self._host = host or os.getenv("POSTGRES_HOST") or os.getenv("DB_HOST", "localhost")
        self._port = port or int(os.getenv("POSTGRES_PORT") or os.getenv("DB_PORT", "5432"))
        self._dbname = dbname or os.getenv("POSTGRES_DB", "analyzer_db")
        self._user = user or os.getenv("POSTGRES_USER", "analyzer")
        self._password = password or os.getenv("POSTGRES_PASSWORD", "analyzer_pg_123")

        logger.info(
            "DirectSQLExecutor initialisé — %s:%d/%s (user=%s)",
            self._host,
            self._port,
            self._dbname,
            self._user,
        )

    def _get_connection(self, timeout_s: float):
        """Crée une connexion psycopg2 read-only."""
        import psycopg2
        import psycopg2.extras

        conn = psycopg2.connect(
            host=self._host,
            port=self._port,
            dbname=self._dbname,
            user=self._user,
            password=self._password,
            connect_timeout=10,
            options=f"-c statement_timeout={int(timeout_s * 1000)}",
        )
        # Forcer le mode read-only
        conn.set_session(readonly=True, autocommit=True)
        return conn

    async def execute(self, sql: str, timeout_s: float = 30.0) -> ExecutionResult:
        """
        Exécute la requête SQL.

        Note : psycopg2 est synchrone. Pour une vraie async, il faudrait
        asyncpg. On utilise psycopg2 pour la simplicité et la compatibilité
        avec le futur Sandbox (qui exécutera du code synchrone dans un
        conteneur). L'Executor wraps dans run_in_executor pour ne pas
        bloquer l'event loop.
        """
        import asyncio

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._execute_sync, sql, timeout_s
        )

    def _execute_sync(self, sql: str, timeout_s: float) -> ExecutionResult:
        """Exécution synchrone (appelée dans un thread)."""
        import psycopg2
        import psycopg2.extras

        t0 = time.perf_counter()
        conn = None
        try:
            conn = self._get_connection(timeout_s)
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute(sql)

            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description] if cursor.description else []

            # Convertir les RealDictRow en dicts normaux
            # et les Decimal en float pour la sérialisation JSON
            records = []
            for row in rows:
                record = {}
                for key, value in dict(row).items():
                    if hasattr(value, "__float__"):
                        record[key] = float(value)
                    else:
                        record[key] = value
                records.append(record)

            duration = time.perf_counter() - t0
            cursor.close()

            logger.info(
                "SQL exécuté — %d rows, %d cols, %.3fs",
                len(records),
                len(columns),
                duration,
            )
            return ExecutionResult(
                success=True,
                records=records,
                columns=columns,
                row_count=len(records),
                duration_s=duration,
            )

        except psycopg2.extensions.QueryCanceledError:
            duration = time.perf_counter() - t0
            logger.warning("SQL timeout après %.1fs", duration)
            return ExecutionResult.failed(
                f"Timeout : requête annulée après {timeout_s}s",
                duration_s=duration,
            )

        except psycopg2.Error as e:
            duration = time.perf_counter() - t0
            logger.error("SQL error : %s", e)
            return ExecutionResult.failed(
                f"PostgreSQL error : {e}",
                duration_s=duration,
            )

        except Exception as e:
            duration = time.perf_counter() - t0
            logger.error("Execution error : %s", e)
            return ExecutionResult.failed(
                f"{type(e).__name__} : {e}",
                duration_s=duration,
            )

        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    async def is_healthy(self) -> bool:
        """Test de connexion."""
        try:
            result = await self.execute("SELECT 1 AS health", timeout_s=5.0)
            return result.success
        except Exception:
            return False