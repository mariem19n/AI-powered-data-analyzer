"""
app/orchestrator/planner.py
Génération du plan d'exécution = f(intent, semantic_context).

Code Python pur — aucun appel LLM.

Le plan dépend à la fois de l'intent (structure globale) et du
SemanticContext (adaptation fine selon entités, tables, métriques).

Exemples :
  - aggregation simple → 1 SQL Agent
  - comparison 2 entités → 2 SQL Agents + 1 Analyse
  - correlation 2 entités même table → N SQL Agents (1 par entité) + 1 Analyse
  - correlation cross-table → N SQL Agents (1 par table) + 1 Analyse
  - diagnosis avec sentiment → SQL + anomalies + SQL contexte + corrélation sentiment

Signature composite :
  Utilisée pour matcher dans le KG des plans passés ayant produit
  un score de feedback ≥ 4. Inclut intent + forme du SemanticContext
  (tables impliquées, nombre d'entités, présence de sentiment, etc.).
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any

from app.orchestrator.schemas import (
    AgentType,
    ExecutionPlan,
    ExecutionStep,
    Intent,
    IntentType,
)

logger = logging.getLogger(__name__)


# Colonnes par défaut à analyser pour anomaly_detection quand l'utilisateur
# n'a précisé aucune métrique explicite (voir _enrich_context_for_anomaly).
DEFAULT_ANOMALY_COLUMNS_BY_ENTITY: dict[str, list[dict[str, str]]] = {
    "crypto": [
        {
            "name": "prix",
            "table": "fact_crypto_daily",
            "column": "close_usd",
            "description": "Prix de clôture par défaut pour analyse d'anomalies",
        },
        {
            "name": "volume",
            "table": "fact_crypto_daily",
            "column": "volume",
            "description": "Volume par défaut pour analyse d'anomalies",
        },
    ],
}


# Colonnes par défaut à extraire pour correlation quand l'utilisateur
# n'a précisé aucune métrique. Une corrélation cross-entity nécessite
# une série numérique par entité — on prend le close_usd par défaut
# pour les cryptos.
DEFAULT_CORRELATION_COLUMNS_BY_ENTITY: dict[str, list[dict[str, str]]] = {
    "crypto": [
        {
            "name": "prix",
            "table": "fact_crypto_daily",
            "column": "close_usd",
            "description": "Prix de clôture par défaut pour analyse de corrélation",
        },
    ],
    "macro_indicator": [
        {
            "name": "valeur",
            "table": "fact_fred_observation",
            "column": "value",
            "description": "Valeur de l'indicateur macro pour analyse de corrélation",
        },
    ],
}


def _enrich_context_for_anomaly(ctx: dict) -> dict:
    """
    Retourne une COPIE enrichie du SemanticContext pour anomaly_detection.

    Règles :
      1. Si ctx.columns déjà non-vide → on respecte le choix utilisateur
      2. Sinon, si entité crypto → ajoute close_usd + volume
      3. Les colonnes sont aussi propagées dans tables[0].columns_used
    """
    import copy as _copy

    existing_columns = ctx.get("columns") or []
    if existing_columns:
        return ctx

    entity_filters = ctx.get("entity_filters") or []
    entity_types = {
        ef.get("entity_type")
        for ef in entity_filters
        if isinstance(ef, dict) and ef.get("entity_type")
    }

    default_cols = None
    for et in entity_types:
        if et in DEFAULT_ANOMALY_COLUMNS_BY_ENTITY:
            default_cols = DEFAULT_ANOMALY_COLUMNS_BY_ENTITY[et]
            break

    if not default_cols:
        return ctx

    enriched_ctx = _copy.deepcopy(ctx)
    enriched_ctx["columns"] = list(default_cols)

    tables = enriched_ctx.get("tables") or []
    if tables and isinstance(tables[0], dict):
        primary_table = tables[0]
        if primary_table.get("table_name") == "fact_crypto_daily":
            existing_cols_used = primary_table.get("columns_used") or []
            new_cols = [c["column"] for c in default_cols]
            merged = list(existing_cols_used)
            for col in new_cols:
                if col not in merged:
                    merged.append(col)
            primary_table["columns_used"] = merged

    return enriched_ctx


def _enrich_context_for_correlation(ctx: dict, entity_filter: dict) -> dict:
    """
    Retourne une COPIE enrichie du SemanticContext pour un step SQL
    de corrélation, scopée sur UNE seule entité.

    Une corrélation cross-entity demande une série numérique par entité.
    Comme l'utilisateur n'a précisé aucune métrique (cf. requires_terms),
    on injecte ici la colonne adéquate selon le type d'entité.

    Règles :
      1. Filtres : on remplace entity_filters par UNIQUEMENT cette entité,
         pour que le SQL Agent ne fasse pas `WHERE symbol='BTC' AND symbol='ETH'`.
      2. Colonnes : si ctx.columns vide, on injecte close_usd (crypto) ou
         value (macro) selon le type de l'entité.
      3. tables[0].columns_used : propagation des colonnes pour que le
         SELECT inclue date + colonne-valeur.

    Args:
        ctx : SemanticContext brut
        entity_filter : un seul élément de ctx.entity_filters

    Returns:
        SemanticContext deep-copié, scopé sur cette entité, prêt pour SQL.
    """
    import copy as _copy

    enriched_ctx = _copy.deepcopy(ctx)

    # 1. Filtres : restreindre à cette entité uniquement.
    enriched_ctx["entity_filters"] = [_copy.deepcopy(entity_filter)]

    # 2. Détecter le type d'entité pour choisir la colonne par défaut.
    entity_type = entity_filter.get("entity_type") or ""
    default_cols = DEFAULT_CORRELATION_COLUMNS_BY_ENTITY.get(entity_type)

    existing_columns = enriched_ctx.get("columns") or []

    # Si pas de métrique ni colonne déjà choisie, on injecte les défauts.
    if not existing_columns and default_cols:
        enriched_ctx["columns"] = list(default_cols)

    # 3. Propager dans tables[0].columns_used.
    tables = enriched_ctx.get("tables") or []
    target_table = entity_filter.get("table")
    if tables and target_table:
        for tbl in tables:
            if isinstance(tbl, dict) and tbl.get("table_name") == target_table:
                existing_cols_used = tbl.get("columns_used") or []
                new_cols: list[str] = []
                # Toujours utile : la date
                new_cols.append("date")
                # Puis les colonnes-valeurs si on en a injecté
                if not existing_columns and default_cols:
                    for c in default_cols:
                        new_cols.append(c["column"])
                merged = list(existing_cols_used)
                for col in new_cols:
                    if col not in merged:
                        merged.append(col)
                tbl["columns_used"] = merged
                break

    # 4. Sécuriser les filtres au niveau tables[0].filters : retirer les
    #    filtres "symbol = 'X'" qui ne concernent PAS l'entité courante.
    #    Le SQL Agent les recompose à partir d'entity_filters ; les filtres
    #    legacy au niveau table créent le WHERE multi-symbol absurde.
    if tables:
        for tbl in tables:
            if not isinstance(tbl, dict):
                continue
            filters = tbl.get("filters") or []
            entity_value = str(entity_filter.get("value", "")).strip()
            entity_column = str(entity_filter.get("column", "")).strip()
            cleaned: list[str] = []
            for f in filters:
                if not isinstance(f, str):
                    cleaned.append(f)
                    continue
                # Heuristique : si le filtre contient la colonne de l'entité
                # ET qu'il NE correspond PAS à la valeur courante, on l'écarte.
                if (
                    entity_column
                    and entity_column in f
                    and entity_value not in f
                ):
                    continue
                cleaned.append(f)
            tbl["filters"] = cleaned

    return enriched_ctx



# ─── Signature composite ──────────────────────────────────────


def compute_plan_signature(intent: Intent, semantic_context: dict[str, Any]) -> str:
    """
    Signature composite utilisée pour matcher des plans passés dans le KG.

    Combine :
      - intent (primary + secondary triés)
      - nombre d'entités, métriques, colonnes, time_filters
      - ensemble des tables impliquées
      - présence de sentiment / macro

    Args:
        intent : intent détecté
        semantic_context : SemanticContext sérialisé (to_dict)

    Returns:
        str : hash hex court de la signature
    """
    tables = sorted(t["table_name"] for t in semantic_context.get("tables", []))

    sig_dict = {
        "intent_primary": intent.primary.value,
        "intent_secondary": sorted(i.value for i in intent.secondary),
        "entity_count": len(semantic_context.get("entity_filters", [])),
        "metric_count": len(semantic_context.get("metrics", [])),
        "column_count": len(semantic_context.get("columns", [])),
        "time_filter_count": len(semantic_context.get("time_filters", [])),
        "tables": tables,
        "has_sentiment": "fact_gdelt_events" in tables,
        "has_macro": "fact_fred_observation" in tables,
        "has_crypto": "fact_crypto_daily" in tables,
    }
    sig_json = json.dumps(sig_dict, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(sig_json.encode()).hexdigest()[:16]


# ─── Plan Generator ───────────────────────────────────────────


class PlanGenerator:
    """
    Génère un ExecutionPlan à partir de (intent, semantic_context).

    Dispatch sur l'intent, puis adaptation fine selon la forme du
    SemanticContext (nombre d'entités, tables, etc.).
    """

    def generate(
        self,
        intent: Intent,
        semantic_context: dict[str, Any],
        external_result: dict[str, Any] | None = None,
    ) -> ExecutionPlan:
        """
        Génère le plan adapté.

        Args:
            intent : résultat de la détection d'intent
            semantic_context : SemanticContext sérialisé
            external_result : résultat Tavily si déjà obtenu en amont
                (ExternalResult.to_dict()). Permet de construire des plans
                external_summary (mode external) ou hybrid_summary
                (intent analytique avec analytic_gaps comblés par Tavily).

        Returns:
            ExecutionPlan prêt à être exécuté
        """
        signature = compute_plan_signature(intent, semantic_context)

        # Dispatch sur l'intent principal
        if intent.primary == IntentType.EXTERNAL_KNOWLEDGE:
            steps = self._external_knowledge_steps(semantic_context, external_result)
        elif intent.primary == IntentType.AGGREGATION:
            steps = self._aggregation_steps(semantic_context, external_result)
        elif intent.primary == IntentType.COMPARISON:
            steps = self._comparison_steps(semantic_context)
        elif intent.primary == IntentType.CORRELATION:
            steps = self._correlation_steps(semantic_context)
        elif intent.primary == IntentType.ANOMALY_DETECTION:
            steps = self._anomaly_steps(semantic_context)
        elif intent.primary == IntentType.FORECASTING:
            steps = self._forecasting_steps(semantic_context)
        elif intent.primary == IntentType.DIAGNOSIS:
            steps = self._diagnosis_steps(semantic_context, intent)
        else:
            # UNKNOWN — plan minimal d'extraction best-effort
            steps = self._fallback_steps(semantic_context)

        plan = ExecutionPlan(
            plan_id=str(uuid.uuid4()),
            intent=intent.primary,
            signature=signature,
            steps=steps,
        )
        logger.info(
            "Plan généré — intent=%s, %d étapes, signature=%s",
            intent.primary.value,
            len(steps),
            signature,
        )
        return plan

    # ─── Helper Tavily ────────────────────────────────────────

    @staticmethod
    def _build_tavily_payload_for_analysis(
        external_result: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """
        Extrait du résultat Tavily UNIQUEMENT les champs utiles aux tasks
        external_summary / hybrid_summary :
          - sources (title, url, snippet, score)
          - extracted_content
          - query

        On EXCLUT volontairement `answer` Tavily : on veut que le LLM
        analyse synthétise lui-même à partir du contenu brut + sources,
        plutôt que de régurgiter la réponse pré-mâchée de Tavily.
        """
        if not isinstance(external_result, dict):
            return None
        if not external_result.get("sources") and not external_result.get(
            "extracted_content"
        ):
            return None
        return {
            "query": external_result.get("query", ""),
            "source": external_result.get("source", "tavily"),
            "provider": external_result.get("provider", "tavily"),
            "sources": list(external_result.get("sources", [])),
            "extracted_content": external_result.get("extracted_content", ""),
        }

    # ─── EXTERNAL_KNOWLEDGE ───────────────────────────────────

    @classmethod
    def _external_knowledge_steps(
        cls,
        ctx: dict[str, Any],
        external_result: dict[str, Any] | None = None,
    ) -> list[ExecutionStep]:
        """
        Plan mono-step pour les questions de type EXTERNAL_KNOWLEDGE.

        Pas de SQL : Tavily est déjà passé par l'Orchestrator, son résultat
        est injecté DIRECTEMENT dans l'instruction du step Analysis.
        """
        tavily_payload = cls._build_tavily_payload_for_analysis(external_result)
        return [
            ExecutionStep(
                step_id="analyse_1",
                agent=AgentType.ANALYSIS_AGENT,
                description="Résumé pédagogique à partir des sources externes",
                instruction={
                    "task": "external_summary",
                    "tavily_payload": tavily_payload,
                    "semantic_context": ctx,
                },
                depends_on=[],
                parallelizable=False,
            ),
        ]

    # ─── AGGREGATION ──────────────────────────────────────────

    @classmethod
    def _aggregation_steps(
        cls,
        ctx: dict[str, Any],
        external_result: dict[str, Any] | None = None,
    ) -> list[ExecutionStep]:
        """
        Pour une agrégation : SQL + Analyse.

        Mode HYBRID : si le SemanticContext contient des `analytic_gaps`
        ET qu'un external_result Tavily est disponible, l'Analyse devient
        task='hybrid_summary' qui synthétise SQL + Tavily ensemble.
        """
        analytic_gaps = ctx.get("analytic_gaps") or []
        is_hybrid = bool(analytic_gaps) and external_result is not None

        sql_step = ExecutionStep(
            step_id="sql_1",
            agent=AgentType.SQL_AGENT,
            description="Extraire les données demandées",
            instruction={
                "task": "extract",
                "semantic_context": ctx,
            },
        )

        if is_hybrid:
            tavily_payload = cls._build_tavily_payload_for_analysis(external_result)
            analysis_step = ExecutionStep(
                step_id="analyse_1",
                agent=AgentType.ANALYSIS_AGENT,
                description="Synthèse hybride : données internes + sources externes",
                instruction={
                    "task": "hybrid_summary",
                    "input_steps": ["sql_1"],
                    "semantic_context": ctx,
                    "tavily_payload": tavily_payload,
                    "analytic_gaps": list(analytic_gaps),
                },
                depends_on=["sql_1"],
            )
        else:
            analysis_step = ExecutionStep(
                step_id="analyse_1",
                agent=AgentType.ANALYSIS_AGENT,
                description="Résumé descriptif et insights",
                instruction={
                    "task": "descriptive",
                    "input_steps": ["sql_1"],
                    "semantic_context": ctx,
                },
                depends_on=["sql_1"],
            )

        return [sql_step, analysis_step]

    # ─── COMPARISON ───────────────────────────────────────────

    def _comparison_steps(self, ctx: dict[str, Any]) -> list[ExecutionStep]:
        entities = ctx.get("entity_filters", [])
        metrics = ctx.get("metrics", [])
        periods = ctx.get("time_filters", [])

        # Cas simple : 2 entités, 1 métrique, 1 période → 2 SQL + 1 Analyse
        # Cas multi-dim : on fait une seule SQL avec GROUP BY côté SQL Agent,
        # puis l'Analyse Agent reformate en long/wide.
        is_simple = (
            len(entities) == 2
            and len(metrics) <= 1
            and len(periods) <= 1
        )

        if is_simple and len(entities) == 2:
            steps = []
            for i, entity in enumerate(entities, start=1):
                steps.append(
                    ExecutionStep(
                        step_id=f"sql_{i}",
                        agent=AgentType.SQL_AGENT,
                        description=f"Extraire données pour {entity.get('entity_name', '?')}",
                        instruction={
                            "task": "extract",
                            "semantic_context": ctx,
                            "entity_filter_override": entity,
                        },
                        parallelizable=True,
                    )
                )
            steps.append(
                ExecutionStep(
                    step_id="analyse_1",
                    agent=AgentType.ANALYSIS_AGENT,
                    description="Comparer les deux séries",
                    instruction={
                        "task": "comparison",
                        "input_steps": [s.step_id for s in steps],
                    },
                    depends_on=[s.step_id for s in steps],
                )
            )
            return steps

        # Cas multi-dim : une seule SQL + analyse multi-dimensionnelle
        return [
            ExecutionStep(
                step_id="sql_1",
                agent=AgentType.SQL_AGENT,
                description="Extraire données groupées pour comparaison multi-dim",
                instruction={
                    "task": "extract",
                    "semantic_context": ctx,
                    "group_by_entities": True,
                },
            ),
            ExecutionStep(
                step_id="analyse_1",
                agent=AgentType.ANALYSIS_AGENT,
                description="Comparaison multi-dimensionnelle",
                instruction={
                    "task": "multi_dim_comparison",
                    "input_steps": ["sql_1"],
                },
                depends_on=["sql_1"],
            ),
        ]

    # ─── CORRELATION ──────────────────────────────────────────

    @staticmethod
    def _correlation_steps(ctx: dict[str, Any]) -> list[ExecutionStep]:
        """
        Plan pour correlation.

        Stratégie : émettre UN step SQL par série à corréler, suivi d'UN
        step Analysis qui consomme tous les steps SQL en `input_steps`.

        Trois sous-cas :
          1. ≥ 2 entités, même table (ex: BTC vs ETH) :
             → 1 step SQL par entité, chacun scopé sur cette entité
               (entity_filter_override + columns_used forcés à date+close_usd).
             → 1 step Analysis avec input_steps = [sql_1, sql_2, ...].

          2. ≥ 2 tables (cross-table, ex: BTC vs taux Fed) :
             → 1 step SQL par table, chacun avec table_subset adapté.
             → 1 step Analysis cross_table=True, input_steps = [sql_1, sql_2].

          3. Cas dégénéré (< 2 entités et < 2 tables) :
             → fallback : 1 step SQL + 1 step Analysis, l'Analysis lèvera
               un warning "moins de 2 séries — corrélation impossible".

        Important : pour le cas 1, on s'inspire du pattern de
        _comparison_steps qui marche déjà. Le SemanticContext est
        deep-copié et scopé via _enrich_context_for_correlation pour
        éviter le WHERE multi-symbol absurde et garantir que close_usd
        est bien extrait même si l'utilisateur n'a précisé aucune
        métrique (cas typique "corrélation entre BTC et ETH").
        """
        entities = ctx.get("entity_filters") or []
        tables_in_ctx = {
            t.get("table_name")
            for t in ctx.get("tables", [])
            if isinstance(t, dict) and t.get("table_name")
        }
        cross_table = len(tables_in_ctx) >= 2

        # ── Cas 1 : ≥ 2 entités sur la même table ─────────────
        if not cross_table and len(entities) >= 2:
            steps: list[ExecutionStep] = []
            for i, entity in enumerate(entities, start=1):
                scoped_ctx = _enrich_context_for_correlation(ctx, entity)
                entity_label = entity.get("entity_name") or entity.get("value") or f"série {i}"
                steps.append(
                    ExecutionStep(
                        step_id=f"sql_{i}",
                        agent=AgentType.SQL_AGENT,
                        description=f"Extraire série temporelle pour {entity_label}",
                        instruction={
                            "task": "extract",
                            "semantic_context": scoped_ctx,
                            "entity_filter_override": entity,
                        },
                        parallelizable=True,
                    )
                )
            input_step_ids = [s.step_id for s in steps]
            steps.append(
                ExecutionStep(
                    step_id="analyse_1",
                    agent=AgentType.ANALYSIS_AGENT,
                    description="Corrélation entre N séries (même table)",
                    instruction={
                        "task": "correlation",
                        "cross_table": False,
                        "input_steps": input_step_ids,
                    },
                    depends_on=list(input_step_ids),
                )
            )
            return steps

        # ── Cas 2 : cross-table (≥ 2 tables) ──────────────────
        if cross_table:
            steps = [
                ExecutionStep(
                    step_id="sql_1",
                    agent=AgentType.SQL_AGENT,
                    description="Extraire série A (table primaire)",
                    instruction={
                        "task": "extract",
                        "semantic_context": ctx,
                        "table_subset": "primary",
                    },
                    parallelizable=True,
                ),
                ExecutionStep(
                    step_id="sql_2",
                    agent=AgentType.SQL_AGENT,
                    description="Extraire série B (table secondaire / filter)",
                    instruction={
                        "task": "extract",
                        "semantic_context": ctx,
                        "table_subset": "filter",
                    },
                    parallelizable=True,
                ),
                ExecutionStep(
                    step_id="analyse_1",
                    agent=AgentType.ANALYSIS_AGENT,
                    description="Alignement temporel + corrélation cross-table",
                    instruction={
                        "task": "correlation",
                        "cross_table": True,
                        "input_steps": ["sql_1", "sql_2"],
                    },
                    depends_on=["sql_1", "sql_2"],
                ),
            ]
            return steps

        # ── Cas 3 : fallback (< 2 entités, < 2 tables) ────────
        # La corrélation a besoin d'au moins 2 séries — la task lèvera un
        # warning explicite ("Moins de 2 séries exploitables").
        logger.warning(
            "Plan correlation dégénéré : %d entités, %d tables. "
            "La task correlation produira un warning.",
            len(entities),
            len(tables_in_ctx),
        )
        return [
            ExecutionStep(
                step_id="sql_1",
                agent=AgentType.SQL_AGENT,
                description="Extraction best-effort (corrélation dégénérée)",
                instruction={
                    "task": "extract",
                    "semantic_context": ctx,
                },
            ),
            ExecutionStep(
                step_id="analyse_1",
                agent=AgentType.ANALYSIS_AGENT,
                description="Corrélation (sera dégénérée — moins de 2 séries)",
                instruction={
                    "task": "correlation",
                    "cross_table": False,
                    "input_steps": ["sql_1"],
                },
                depends_on=["sql_1"],
            ),
        ]

    # ─── ANOMALY DETECTION ────────────────────────────────────

    @staticmethod
    def _anomaly_steps(ctx: dict[str, Any]) -> list[ExecutionStep]:
        """
        Plan pour anomaly_detection.

        Si l'utilisateur n'a précisé aucune colonne et que l'entité est crypto,
        on enrichit le SemanticContext envoyé au SQL Agent avec close_usd +
        volume (voir _enrich_context_for_anomaly). Cela permet à
        Isolation Forest de se déclencher côté Analysis Agent.
        """
        enriched_ctx = _enrich_context_for_anomaly(ctx)
        return [
            ExecutionStep(
                step_id="sql_1",
                agent=AgentType.SQL_AGENT,
                description="Extraire série temporelle",
                instruction={
                    "task": "extract",
                    "semantic_context": enriched_ctx,
                },
            ),
            ExecutionStep(
                step_id="analyse_1",
                agent=AgentType.ANALYSIS_AGENT,
                description="Détection d'anomalies (IQR / Z-score / Isolation Forest auto)",
                instruction={
                    "task": "anomaly_detection",
                    "input_steps": ["sql_1"],
                    "semantic_context": enriched_ctx,
                },
                depends_on=["sql_1"],
            ),
        ]

    # ─── FORECASTING ──────────────────────────────────────────

    @staticmethod
    def _extract_horizon_days(ctx: dict[str, Any], default: int = 30) -> int:
        """
        Extrait l'horizon de prévision depuis les time_filters du SemanticContext.

        Cherche un pattern numérique dans raw_text :
          "7 prochains jours" → 7
          "next 14 days"      → 14
          "30 jours"          → 30

        Si aucun match → retourne `default`.
        """
        import re

        for tf in ctx.get("time_filters", []):
            raw = tf.get("raw_text", "")
            m = re.search(
                r"(\d+)\s*(?:prochains?\s*jours?|next\s*days?|jours?)",
                raw,
                re.IGNORECASE,
            )
            if m:
                return int(m.group(1))
        return default

    @staticmethod
    def _forecasting_steps(ctx: dict[str, Any]) -> list[ExecutionStep]:
        horizon = PlanGenerator._extract_horizon_days(ctx)
        return [
            ExecutionStep(
                step_id="sql_1",
                agent=AgentType.SQL_AGENT,
                description="Extraire l'historique",
                instruction={
                    "task": "extract",
                    "semantic_context": ctx,
                    "extend_history": True,
                },
            ),
            ExecutionStep(
                step_id="analyse_1",
                agent=AgentType.ANALYSIS_AGENT,
                description="Prévision (Prophet / Sklearn)",
                instruction={
                    "task": "forecasting",
                    "input_steps": ["sql_1"],
                    "horizon_days": horizon,
                },
                depends_on=["sql_1"],
            ),
        ]

    # ─── DIAGNOSIS ────────────────────────────────────────────

    @staticmethod
    def _diagnosis_steps(
        ctx: dict[str, Any], intent: Intent
    ) -> list[ExecutionStep]:
        """
        Intent composite. Le plan s'adapte selon la présence
        de sentiment et/ou macro dans le SemanticContext.
        """
        tables = {t["table_name"] for t in ctx.get("tables", [])}
        has_sentiment = "fact_gdelt_events" in tables
        has_macro = "fact_fred_observation" in tables

        steps = [
            ExecutionStep(
                step_id="sql_main",
                agent=AgentType.SQL_AGENT,
                description="Extraire la série principale (l'objet du diagnostic)",
                instruction={
                    "task": "extract",
                    "semantic_context": ctx,
                    "table_subset": "primary",
                },
            ),
            ExecutionStep(
                step_id="analyse_anomalies",
                agent=AgentType.ANALYSIS_AGENT,
                description="Détecter les anomalies / mouvements significatifs",
                instruction={
                    "task": "anomaly_detection",
                    "input_steps": ["sql_main"],
                },
                depends_on=["sql_main"],
            ),
        ]

        context_steps: list[str] = []

        if has_macro:
            steps.append(
                ExecutionStep(
                    step_id="sql_macro",
                    agent=AgentType.SQL_AGENT,
                    description="Extraire contexte macro",
                    instruction={
                        "task": "extract",
                        "semantic_context": ctx,
                        "table_subset": "macro",
                    },
                    parallelizable=True,
                )
            )
            context_steps.append("sql_macro")

        if has_sentiment:
            steps.append(
                ExecutionStep(
                    step_id="sql_sentiment",
                    agent=AgentType.SQL_AGENT,
                    description="Extraire contexte sentiment",
                    instruction={
                        "task": "extract",
                        "semantic_context": ctx,
                        "table_subset": "sentiment",
                    },
                    parallelizable=True,
                )
            )
            context_steps.append("sql_sentiment")

        if context_steps:
            steps.append(
                ExecutionStep(
                    step_id="analyse_correlate",
                    agent=AgentType.ANALYSIS_AGENT,
                    description="Corréler anomalies avec le contexte macro/sentiment",
                    instruction={
                        "task": "causal_correlation",
                        "input_steps": ["analyse_anomalies", *context_steps],
                    },
                    depends_on=["analyse_anomalies", *context_steps],
                )
            )
            final_inputs = [
                "sql_main",
                "analyse_anomalies",
                "analyse_correlate",
            ]
            final_depends = ["analyse_correlate"]
        else:
            final_inputs = ["sql_main", "analyse_anomalies"]
            final_depends = ["analyse_anomalies"]

        steps.append(
            ExecutionStep(
                step_id="analyse_report",
                agent=AgentType.ANALYSIS_AGENT,
                description="Rapport diagnostic consolidé",
                instruction={
                    "task": "diagnostic_report",
                    "input_steps": final_inputs,
                },
                depends_on=final_depends,
            )
        )
        return steps

    # ─── FALLBACK ─────────────────────────────────────────────

    @staticmethod
    def _fallback_steps(ctx: dict[str, Any]) -> list[ExecutionStep]:
        """Plan minimal best-effort quand l'intent est UNKNOWN."""
        return [
            ExecutionStep(
                step_id="sql_1",
                agent=AgentType.SQL_AGENT,
                description="Extraction best-effort",
                instruction={
                    "task": "extract",
                    "semantic_context": ctx,
                },
            ),
        ]