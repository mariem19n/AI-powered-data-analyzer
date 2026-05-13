"""
app/agents/sql_agent.py
SQL Agent — le seul composant autorisé à interroger PostgreSQL.

Implémente l'interface AgentRunner de l'Orchestrator.

Flow complet :
  1. Recevoir l'instruction (SemanticContext + task)
  2. Vérifier le cache Redis (context_hash → DataFrame)
  3. Générer le SQL :
     a. Template Python (cas simples, ~80%)
     b. LLM si le template ne peut pas gérer (cas complexes, ~20%)
  4. Vérification de sécurité (SecurityChecker)
  5. Exécution via SQLExecutor (psycopg2 direct / futur Sandbox)
  6. Validation du résultat (OutputValidator)
  7. Stockage en cache Redis
  8. Retourner le DataFrame + métadonnées

Séparation des responsabilités :
  - Le SQL Agent NE résout PAS les termes métier (c'est le Semantic Layer)
  - Il NE détecte PAS l'intent (c'est l'Orchestrator)
  - Il NE fait PAS d'analyse (c'est l'Analyse Agent)
  - Il ASSEMBLE le SQL et EXÉCUTE la requête
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.agents.sql_executor import DirectSQLExecutor, SQLExecutor
from app.agents.sql_templates import SQLTemplateEngine, TemplateResult
from app.agents.sql_validator import OutputValidator, ValidationResult
from app.sandbox.security import SQLSecurityChecker

logger = logging.getLogger(__name__)


class SQLAgent:
    """
    SQL Agent — extrait les données depuis PostgreSQL.

    Implémente l'interface AgentRunner :
        async def run(instruction, upstream_results) -> dict

    Usage :
        agent = SQLAgent(
            executor=DirectSQLExecutor(),
            redis_client=redis_client,  # optionnel
        )
        result = await agent.run(
            instruction={"task": "extract", "semantic_context": {...}},
            upstream_results={},
        )
    """

    def __init__(
        self,
        executor: SQLExecutor | None = None,
        redis_client: Any = None,
        llm_client: Any = None,
        max_retries: int = 1,
    ):
        self._executor = executor or DirectSQLExecutor()
        self._redis = redis_client
        self._llm = llm_client
        self._template_engine = SQLTemplateEngine()
        self._security_checker = SQLSecurityChecker()
        self._validator = OutputValidator()
        self._max_retries = max_retries

        logger.info("SQLAgent initialisé")

    # ─── Interface AgentRunner ────────────────────────────────

    async def run(
        self,
        instruction: dict[str, Any],
        upstream_results: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Point d'entrée appelé par le PlanExecutor.

        Args:
            instruction: {
                "task": "extract",
                "semantic_context": { ... SemanticContext.to_dict() ... },
                "entity_filter_override": { ... } (optionnel, pour comparison),
                "limit": 1000 (optionnel),
            }
            upstream_results: résultats des étapes précédentes (souvent vide pour SQL)

        Returns:
            {
                "records": [...],    # DataFrame.to_dict('records')
                "columns": [...],
                "row_count": int,
                "sql": "...",         # SQL généré (audit)
                "method": "template" | "llm",
                "validation": { ... },
                "cached": bool,
                "duration_s": float,
            }
        """
        t0 = time.perf_counter()

        ctx = instruction.get("semantic_context", {})
        if not ctx:
            return self._error_result(
                "Aucun SemanticContext dans l'instruction", t0
            )

        context_hash = ctx.get("context_hash", "")

        # ── 1. Cache check (niveau SQL) ──────────────────────
        cached = await self._check_sql_cache(context_hash, instruction)
        if cached is not None:
            cached["cached"] = True
            cached["duration_s"] = round(time.perf_counter() - t0, 3)
            logger.info("SQL cache HIT [%s]", context_hash[:12])
            return cached

        # ── 2. Génération SQL (template puis LLM fallback) ───
        sql, method = await self._generate_sql(ctx, instruction)
        if sql is None:
            return self._error_result(
                "Impossible de générer le SQL", t0
            )

        # ── 3. Security check ────────────────────────────────
        sec_result = self._security_checker.check(sql)
        if not sec_result.is_safe:
            logger.error("SQL bloqué par security checker : %s", sec_result.reason)
            return self._error_result(
                f"SQL bloqué : {sec_result.reason}", t0
            )

        # ── 4. Exécution ─────────────────────────────────────
        timeout = instruction.get("timeout_s", 30.0)
        exec_result = await self._executor.execute(sql, timeout_s=timeout)

        if not exec_result.success:
            # Retry une fois si c'est une erreur SQL (pas un timeout)
            if (
                self._max_retries > 0
                and "timeout" not in exec_result.error.lower()
            ):
                logger.info("SQL retry — erreur : %s", exec_result.error[:100])
                retry_sql, retry_method = await self._generate_sql_llm(
                    ctx, instruction, previous_error=exec_result.error
                )
                if retry_sql:
                    sec2 = self._security_checker.check(retry_sql)
                    if sec2.is_safe:
                        exec_result = await self._executor.execute(
                            retry_sql, timeout_s=timeout
                        )
                        if exec_result.success:
                            sql = retry_sql
                            method = retry_method

            if not exec_result.success:
                return self._error_result(
                    f"Exécution SQL échouée : {exec_result.error}", t0
                )

        # ── 5. Validation ────────────────────────────────────
        validation = self._validator.validate(
            records=exec_result.records,
            columns=exec_result.columns,
            semantic_context=ctx,
        )

        # ── 6. Cache store (seulement si résultat non vide et valide) ─
        result = {
            "records": exec_result.records,
            "columns": exec_result.columns,
            "row_count": exec_result.row_count,
            "sql": sql,
            "method": method,
            "validation": {
                "is_valid": validation.is_valid,
                "confidence": validation.confidence,
                "warnings": validation.warnings,
                "errors": validation.errors,
            },
            "cached": False,
            "duration_s": round(time.perf_counter() - t0, 3),
        }

        # Ne pas cacher les résultats vides ou invalides
        should_cache_sql = (
            exec_result.success
            and not result.get("partial", False)
            and exec_result.row_count > 0
            and bool(exec_result.records)
            and validation.is_valid
        )
        if should_cache_sql:
            await self._store_sql_cache(context_hash, instruction, result)
        else:
            logger.info(
                "SQL result NOT cached — rows=%d, valid=%s",
                exec_result.row_count,
                validation.is_valid,
            )

        logger.info(
            "SQL Agent — method=%s, %d rows, %.3fs, valid=%s (conf=%.2f)",
            method,
            exec_result.row_count,
            result["duration_s"],
            validation.is_valid,
            validation.confidence,
        )
        return result

    # ─── Génération SQL ───────────────────────────────────────

    async def _generate_sql(
        self,
        ctx: dict[str, Any],
        instruction: dict[str, Any],
    ) -> tuple[str | None, str]:
        """
        Stratégie hybride : template d'abord, LLM si nécessaire.

        Returns:
            (sql, method) — method est "template" ou "llm"
        """
        # Essayer le template d'abord
        template_result = self._template_engine.build(ctx, instruction)

        if template_result.sql is not None:
            logger.info("SQL via template : %s", template_result.sql[:100])
            return template_result.sql, "template"

        logger.info(
            "Template ne peut pas gérer — fallback LLM : %s",
            template_result.reason,
        )

        # Fallback LLM
        sql, method = await self._generate_sql_llm(ctx, instruction)
        return sql, method

    async def _generate_sql_llm(
        self,
        ctx: dict[str, Any],
        instruction: dict[str, Any],
        previous_error: str = "",
    ) -> tuple[str | None, str]:
        """
        Génère le SQL via le LLM centralisé.

        Le LLM reçoit le SemanticContext complet (tables, colonnes,
        filtres déjà résolus) et assemble la requête finale.
        """
        if self._llm is None:
            try:
                from app.llm import get_llm_client
                self._llm = get_llm_client()
            except Exception:
                logger.warning("LLM non disponible — pas de fallback SQL")
                return None, "llm"

        # Construire le prompt avec le SemanticContext
        system_prompt = self._build_llm_system_prompt()
        user_prompt = self._build_llm_user_prompt(ctx, instruction, previous_error)

        try:
            response = self._llm.chat(
                system=system_prompt,
                user=user_prompt,
                purpose="sql_generation",
                max_tokens=1024,
            )

            # Extraire le SQL de la réponse
            sql = self._extract_sql_from_response(response)
            if sql:
                logger.info("SQL via LLM : %s", sql[:100])
                return sql, "llm"
            else:
                logger.warning("LLM n'a pas retourné de SQL valide")
                return None, "llm"

        except Exception as e:
            logger.error("LLM SQL generation error : %s", e)
            return None, "llm"

    @staticmethod
    def _build_llm_system_prompt() -> str:
        return """Tu es un générateur SQL PostgreSQL expert. Tu reçois un SemanticContext
structuré contenant toutes les informations résolues (tables, colonnes, filtres, métriques).

Règles strictes :
1. Génère UNIQUEMENT un SELECT. Jamais de DROP, DELETE, UPDATE, INSERT.
2. Utilise UNIQUEMENT les tables et colonnes mentionnées dans le SemanticContext.
3. Applique TOUS les filtres (entity_filters, time_filters, implicit_conditions).
4. Pour les métriques avec formule, utilise la formule exacte fournie.
5. Ajoute un LIMIT 1000 si non spécifié.
6. Retourne le SQL pur, sans explication ni markdown.

Tables disponibles dans le système :
- fact_crypto_daily (date, symbol, open_usd, high_usd, low_usd, close_usd, volume, market_cap_usd)
- fact_fred_observation (date, fred_code, value)
- fact_gdelt_events (date, title, url, source_domain, tone, keyword)
- agg_daily_sentiment (date, keyword, avg_tone, article_count)
- agg_monthly_crypto (month, symbol, avg_close, total_volume)
- stg_daily_metrics (date, symbol, close_usd, daily_return, ma_7, ma_30, volatility_30d)
- dim_crypto (crypto_id, symbol, name)
- dim_fred_series (series_id, fred_code, name, description)

Réponds UNIQUEMENT avec le SQL. Pas de markdown, pas de ```, pas d'explication."""

    @staticmethod
    def _build_llm_user_prompt(
        ctx: dict[str, Any],
        instruction: dict[str, Any],
        previous_error: str = "",
    ) -> str:
        parts = [f"SemanticContext :\n{json.dumps(ctx, indent=2, ensure_ascii=False)}"]

        task = instruction.get("task", "extract")
        parts.append(f"\nTask : {task}")

        overrides = {
            k: v for k, v in instruction.items()
            if k not in ("task", "semantic_context") and v
        }
        if overrides:
            parts.append(f"Instructions supplémentaires : {json.dumps(overrides)}")

        if previous_error:
            parts.append(
                f"\nATTENTION : la requête précédente a échoué avec l'erreur :\n"
                f"{previous_error}\n"
                f"Génère une requête corrigée."
            )

        return "\n".join(parts)

    @staticmethod
    def _extract_sql_from_response(response: str) -> str | None:
        """Extrait le SQL pur de la réponse LLM."""
        cleaned = response.strip()

        # Supprimer les blocs markdown ```sql ... ```
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # Enlever la première et dernière ligne (```)
            sql_lines = [
                line for line in lines
                if not line.strip().startswith("```")
            ]
            cleaned = "\n".join(sql_lines).strip()

        # Vérifier que ça commence par SELECT ou WITH
        upper = cleaned.upper().strip()
        if upper.startswith("SELECT") or upper.startswith("WITH"):
            return cleaned

        # Essayer de trouver un SELECT dans la réponse
        select_idx = upper.find("SELECT")
        if select_idx >= 0:
            return cleaned[select_idx:]

        return None

    # ─── Cache SQL ────────────────────────────────────────────

    async def _check_sql_cache(
        self,
        context_hash: str,
        instruction: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Vérifie le cache Redis pour ce SemanticContext."""
        if not self._redis or not context_hash:
            return None

        # La clé inclut le hash du contexte + l'entity override
        # (pour les comparison plans avec plusieurs steps SQL)
        cache_key = self._build_cache_key(context_hash, instruction)

        try:
            cached = await self._redis.get_sql_cache(cache_key)
            return cached
        except Exception as e:
            logger.warning("SQL cache check error : %s", e)
            return None

    async def _store_sql_cache(
        self,
        context_hash: str,
        instruction: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """Stocke le résultat dans le cache Redis."""
        if not self._redis or not context_hash:
            return

        cache_key = self._build_cache_key(context_hash, instruction)

        try:
            # Déterminer si les données sont historiques (TTL plus long)
            is_historical = self._is_historical(instruction)
            await self._redis.set_sql_cache(
                cache_key, result, is_historical=is_historical
            )
            logger.info(
                "SQL cache STORE [%s] — historical=%s",
                cache_key[:16],
                is_historical,
            )
        except Exception as e:
            logger.warning("SQL cache store error : %s", e)

    @staticmethod
    def _build_cache_key(
        context_hash: str,
        instruction: dict[str, Any],
    ) -> str:
        """
        Construit la clé de cache SQL.

        Inclut l'entity override pour distinguer les steps
        de comparison qui partagent le même SemanticContext
        mais filtrent sur des entités différentes.
        """
        override = instruction.get("entity_filter_override")
        if override:
            entity_val = override.get("value", "")
            return f"{context_hash}:{entity_val}"
        return context_hash

    @staticmethod
    def _is_historical(instruction: dict[str, Any]) -> bool:
        """
        Détermine si les données sont historiques (TTL 24h)
        ou transactionnelles (TTL 1h).
        """
        ctx = instruction.get("semantic_context", {})
        time_filters = ctx.get("time_filters", [])

        # Si pas de filtre temporel → historique (large dataset)
        if not time_filters:
            return True

        # Si le filtre contient "CURRENT_DATE" ou "NOW()" → transactionnel
        for tf in time_filters:
            clause = tf.get("filter_clause", "").upper()
            if "CURRENT_DATE" in clause or "NOW()" in clause:
                return False

        return True

    # ─── Helpers ──────────────────────────────────────────────

    @staticmethod
    def _error_result(error: str, t0: float) -> dict[str, Any]:
        return {
            "records": [],
            "columns": [],
            "row_count": 0,
            "sql": "",
            "method": "error",
            "validation": {
                "is_valid": False,
                "confidence": 0.0,
                "warnings": [],
                "errors": [error],
            },
            "cached": False,
            "duration_s": round(time.perf_counter() - t0, 3),
        }
