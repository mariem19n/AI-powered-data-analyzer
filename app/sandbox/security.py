"""
app/sandbox/security.py
Vérification de sécurité pré-exécution SQL.

S'exécute AVANT toute exécution de requête. Bloque :
  - Requêtes destructrices (DROP, DELETE, UPDATE, ALTER, TRUNCATE, INSERT)
  - Tables non autorisées (whitelist stricte)
  - Patterns d'injection SQL courants
  - Commandes PostgreSQL dangereuses (COPY, GRANT, CREATE, etc.)

Double protection :
  1. Ce module filtre en amont (code Python)
  2. PostgreSQL refuse en aval (utilisateur read-only, SELECT uniquement)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ─── Tables autorisées ────────────────────────────────────────

# Whitelist stricte — seules ces tables sont accessibles en lecture.
# Toute requête référençant une table hors de cette liste est rejetée.
ALLOWED_TABLES: set[str] = {
    # Dimension tables
    "dim_crypto",
    "dim_fred_series",
    # Fact tables
    "fact_crypto_daily",
    "fact_crypto_daily_btc",
    "fact_crypto_daily_eth",
    "fact_crypto_daily_sol",
    "fact_crypto_daily_xrp",
    "fact_crypto_daily_ada",
    "fact_crypto_daily_dot",
    "fact_crypto_daily_doge",
    "fact_crypto_daily_avax",
    "fact_crypto_daily_link",
    "fact_crypto_daily_ltc",
    "fact_fred_observation",
    "fact_gdelt_events",
    # Aggregation tables
    "agg_daily_sentiment",
    "agg_monthly_crypto",
    # Staging tables
    "stg_daily_metrics",
}

# ─── Patterns interdits ──────────────────────────────────────

# Mots-clés SQL destructifs — début de statement uniquement
DESTRUCTIVE_KEYWORDS: set[str] = {
    "DROP",
    "DELETE",
    "UPDATE",
    "ALTER",
    "TRUNCATE",
    "INSERT",
    "CREATE",
    "GRANT",
    "REVOKE",
    "COPY",
    "EXECUTE",
    "CALL",
    "DO",
    "SET",
    "RESET",
    "DISCARD",
    "LOCK",
    "VACUUM",
    "ANALYZE",  # PostgreSQL ANALYZE (pas la fonction d'agrégation)
    "REINDEX",
    "CLUSTER",
    "REFRESH",
    "NOTIFY",
    "LISTEN",
    "UNLISTEN",
    "LOAD",
    "DEALLOCATE",
    "PREPARE",
}

# Patterns d'injection SQL courants
INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r";\s*(DROP|DELETE|UPDATE|INSERT|ALTER|CREATE|TRUNCATE)", re.IGNORECASE),
    re.compile(r"--\s*$", re.MULTILINE),  # Commentaires en fin de ligne
    re.compile(r"/\*.*?\*/", re.DOTALL),  # Commentaires bloc
    re.compile(r"xp_\w+", re.IGNORECASE),  # Fonctions système SQL Server
    re.compile(r"pg_\w+\s*\(", re.IGNORECASE),  # Fonctions système PostgreSQL
    re.compile(r"information_schema\.", re.IGNORECASE),  # Méta-tables
    re.compile(r"pg_catalog\.", re.IGNORECASE),  # Catalogue système
]


# ─── Résultat de la vérification ──────────────────────────────


@dataclass
class SecurityCheckResult:
    """Résultat de la vérification de sécurité."""

    is_safe: bool
    reason: str = ""
    blocked_keyword: str = ""
    blocked_table: str = ""

    @staticmethod
    def safe() -> SecurityCheckResult:
        return SecurityCheckResult(is_safe=True)

    @staticmethod
    def blocked(reason: str, **kwargs) -> SecurityCheckResult:
        return SecurityCheckResult(is_safe=False, reason=reason, **kwargs)


# ─── Vérificateur ─────────────────────────────────────────────


class SQLSecurityChecker:
    """
    Vérifie qu'une requête SQL est sûre avant exécution.

    Usage :
        checker = SQLSecurityChecker()
        result = checker.check("SELECT * FROM fact_crypto_daily")
        if not result.is_safe:
            raise SecurityError(result.reason)
    """

    def __init__(
        self,
        allowed_tables: set[str] | None = None,
        allow_write: bool = False,
    ):
        self._allowed_tables = allowed_tables or ALLOWED_TABLES
        self._allow_write = allow_write

    def check(self, sql: str) -> SecurityCheckResult:
        """
        Vérifie une requête SQL.

        Checks dans l'ordre :
          1. SQL non vide
          2. Pas de mots-clés destructifs
          3. Pas de patterns d'injection
          4. Toutes les tables référencées sont dans la whitelist
          5. La requête est un SELECT (ou WITH ... SELECT)

        Returns:
            SecurityCheckResult
        """
        if not sql or not sql.strip():
            return SecurityCheckResult.blocked("Requête SQL vide")

        cleaned = sql.strip()

        # 1. Vérifier les mots-clés destructifs
        result = self._check_destructive_keywords(cleaned)
        if not result.is_safe:
            return result

        # 2. Vérifier les patterns d'injection
        result = self._check_injection_patterns(cleaned)
        if not result.is_safe:
            return result

        # 3. Vérifier que c'est un SELECT
        result = self._check_is_select(cleaned)
        if not result.is_safe:
            return result

        # 4. Vérifier les tables
        result = self._check_tables(cleaned)
        if not result.is_safe:
            return result

        return SecurityCheckResult.safe()

    # ─── Checks individuels ───────────────────────────────────

    def _check_destructive_keywords(self, sql: str) -> SecurityCheckResult:
        """Vérifie qu'aucun mot-clé destructif n'est présent."""
        if self._allow_write:
            return SecurityCheckResult.safe()

        # Extraire le premier mot significatif (ignorer WITH pour les CTE)
        upper = sql.upper().strip()
        first_word = upper.split()[0] if upper.split() else ""

        if first_word in DESTRUCTIVE_KEYWORDS:
            logger.warning("SQL bloqué — mot-clé destructif : %s", first_word)
            return SecurityCheckResult.blocked(
                f"Mot-clé SQL interdit : {first_word}",
                blocked_keyword=first_word,
            )

        # Vérifier aussi dans le body (après un ;)
        for keyword in DESTRUCTIVE_KEYWORDS:
            pattern = re.compile(
                rf";\s*{keyword}\b", re.IGNORECASE
            )
            if pattern.search(sql):
                logger.warning(
                    "SQL bloqué — mot-clé destructif après ';' : %s", keyword
                )
                return SecurityCheckResult.blocked(
                    f"Mot-clé SQL interdit après ';' : {keyword}",
                    blocked_keyword=keyword,
                )

        return SecurityCheckResult.safe()

    @staticmethod
    def _check_injection_patterns(sql: str) -> SecurityCheckResult:
        """Vérifie qu'aucun pattern d'injection n'est présent."""
        for pattern in INJECTION_PATTERNS:
            match = pattern.search(sql)
            if match:
                logger.warning(
                    "SQL bloqué — pattern d'injection détecté : %s",
                    match.group()[:50],
                )
                return SecurityCheckResult.blocked(
                    f"Pattern d'injection SQL détecté : {match.group()[:50]}"
                )
        return SecurityCheckResult.safe()

    @staticmethod
    def _check_is_select(sql: str) -> SecurityCheckResult:
        """Vérifie que la requête commence par SELECT ou WITH."""
        upper = sql.upper().strip()
        if not (upper.startswith("SELECT") or upper.startswith("WITH")):
            first_word = upper.split()[0] if upper.split() else "?"
            logger.warning(
                "SQL bloqué — pas un SELECT : commence par %s", first_word
            )
            return SecurityCheckResult.blocked(
                f"Seules les requêtes SELECT sont autorisées (reçu : {first_word})"
            )
        return SecurityCheckResult.safe()

    def _check_tables(self, sql: str) -> SecurityCheckResult:
        """
        Vérifie que toutes les tables référencées sont dans la whitelist.

        Gère les CTE (WITH ... AS) en extrayant les alias comme
        noms de tables temporaires autorisés pour cette requête.
        """
        # Extraire les alias CTE (WITH cte_name AS (...))
        cte_pattern = re.compile(
            r"WITH\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+AS\s*\(",
            re.IGNORECASE,
        )
        cte_aliases = {m.lower() for m in cte_pattern.findall(sql)}

        # Tables autorisées pour cette requête = whitelist + CTE aliases
        allowed = self._allowed_tables | cte_aliases

        # Pattern pour extraire les noms de tables après FROM et JOIN
        table_pattern = re.compile(
            r"(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
            re.IGNORECASE,
        )
        tables_found = table_pattern.findall(sql)

        # Mots-clés SQL à ignorer (pas des noms de tables)
        sql_keywords = {
            "select", "where", "and", "or", "on", "as", "in",
            "not", "null", "true", "false", "lateral", "unnest",
        }

        for table in tables_found:
            table_lower = table.lower()
            if table_lower in sql_keywords:
                continue
            if table_lower not in allowed:
                logger.warning(
                    "SQL bloqué — table non autorisée : %s", table
                )
                return SecurityCheckResult.blocked(
                    f"Table non autorisée : {table}",
                    blocked_table=table,
                )

        return SecurityCheckResult.safe()