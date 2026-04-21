"""
app/orchestrator/planner.py
Génération du plan d'exécution = f(intent, semantic_context).

Code Python pur — aucun appel LLM.

Le plan dépend à la fois de l'intent (structure globale) et du
SemanticContext (adaptation fine selon entités, tables, métriques).

Exemples :
  - aggregation simple → 1 SQL Agent
  - comparison 2 entités → 2 SQL Agents + 1 Analyse
  - correlation 2 tables → 2 SQL Agents + resampling + correlation
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
    ) -> ExecutionPlan:
        """
        Génère le plan adapté.

        Args:
            intent : résultat de la détection d'intent
            semantic_context : SemanticContext sérialisé

        Returns:
            ExecutionPlan prêt à être exécuté
        """
        signature = compute_plan_signature(intent, semantic_context)

        # Dispatch sur l'intent principal
        if intent.primary == IntentType.AGGREGATION:
            steps = self._aggregation_steps(semantic_context)
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

    # ─── AGGREGATION ──────────────────────────────────────────

    @staticmethod
    def _aggregation_steps(ctx: dict[str, Any]) -> list[ExecutionStep]:
        return [
            ExecutionStep(
                step_id="sql_1",
                agent=AgentType.SQL_AGENT,
                description="Extraire les données demandées",
                instruction={
                    "task": "extract",
                    "semantic_context": ctx,
                },
            ),
        ]

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
        tables = {t["table_name"] for t in ctx.get("tables", [])}
        cross_table = len(tables) >= 2

        if cross_table:
            # Tables différentes : besoin d'alignement temporel avant corrélation
            steps = [
                ExecutionStep(
                    step_id="sql_1",
                    agent=AgentType.SQL_AGENT,
                    description="Extraire série A",
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
                    description="Extraire série B",
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
                    description="Alignement temporel + corrélation",
                    instruction={
                        "task": "correlation",
                        "cross_table": True,
                        "input_steps": ["sql_1", "sql_2"],
                    },
                    depends_on=["sql_1", "sql_2"],
                ),
            ]
        else:
            # Même table : pivot + corrélation directe
            steps = [
                ExecutionStep(
                    step_id="sql_1",
                    agent=AgentType.SQL_AGENT,
                    description="Extraire les séries (même table)",
                    instruction={
                        "task": "extract",
                        "semantic_context": ctx,
                        "pivot_on_entity": True,
                    },
                ),
                ExecutionStep(
                    step_id="analyse_1",
                    agent=AgentType.ANALYSIS_AGENT,
                    description="Corrélation",
                    instruction={
                        "task": "correlation",
                        "cross_table": False,
                        "input_steps": ["sql_1"],
                    },
                    depends_on=["sql_1"],
                ),
            ]
        return steps

    # ─── ANOMALY DETECTION ────────────────────────────────────

    @staticmethod
    def _anomaly_steps(ctx: dict[str, Any]) -> list[ExecutionStep]:
        return [
            ExecutionStep(
                step_id="sql_1",
                agent=AgentType.SQL_AGENT,
                description="Extraire série temporelle",
                instruction={
                    "task": "extract",
                    "semantic_context": ctx,
                },
            ),
            ExecutionStep(
                step_id="analyse_1",
                agent=AgentType.ANALYSIS_AGENT,
                description="Détection d'anomalies (Z-score / IQR / Isolation Forest)",
                instruction={
                    "task": "anomaly_detection",
                    "input_steps": ["sql_1"],
                },
                depends_on=["sql_1"],
            ),
        ]

    # ─── FORECASTING ──────────────────────────────────────────

    @staticmethod
    def _forecasting_steps(ctx: dict[str, Any]) -> list[ExecutionStep]:
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
