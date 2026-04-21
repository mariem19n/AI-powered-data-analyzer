"""
app/orchestrator/aggregator.py
Aggregator — assemble les résultats des étapes en une réponse finale.

Le résultat de l'Analyse Agent contient déjà des insights, des
visualisations Plotly et des recommandations. L'Aggregator les
collecte, fusionne et structure en OrchestratorResponse.

Il ne fait AUCUN appel LLM — l'Analyse Agent a déjà produit le
texte en langage naturel. L'Aggregator est un assembleur.
"""

from __future__ import annotations

import logging
from typing import Any

from app.orchestrator.schemas import (
    AgentType,
    ClarificationRequest,
    ExecutionPlan,
    Intent,
    OrchestratorResponse,
    StepResult,
    StepStatus,
)

logger = logging.getLogger(__name__)


class ResponseAggregator:
    """
    Agrège les step_results en une OrchestratorResponse finale.

    Convention d'output des agents (respectée par les agents réels) :

    SQL Agent.run() retourne :
      {
        "records": [...],          # DataFrame.to_dict('records')
        "columns": [...],
        "row_count": int,
        "sql": "...",              # SQL généré (pour audit)
      }

    Analyse Agent.run() retourne :
      {
        "insights": ["...", ...],
        "visualizations": [{...plotly_json...}, ...],
        "recommendations": ["...", ...],
        "stats": {...},            # optionnel
      }
    """

    def aggregate(
        self,
        session_id: str,
        question: str,
        intent: Intent | None,
        plan: ExecutionPlan | None,
        step_results: dict[str, StepResult],
        total_duration_s: float,
        llm_calls: int,
        cache_hit: bool = False,
        clarification: ClarificationRequest | None = None,
    ) -> OrchestratorResponse:
        """Assemble la réponse finale."""

        response = OrchestratorResponse(
            session_id=session_id,
            question=question,
            intent=intent,
            plan=plan,
            total_duration_s=round(total_duration_s, 3),
            llm_calls=llm_calls,
            cache_hit=cache_hit,
        )

        if clarification is not None:
            response.needs_clarification = True
            response.clarification = clarification
            return response

        if plan is None:
            return response

        # Collecter les échecs
        failed = [
            sid
            for sid, r in step_results.items()
            if r.status in (StepStatus.FAILED, StepStatus.SKIPPED)
        ]
        response.failed_steps = failed
        response.partial = bool(failed) and len(failed) < len(step_results)

        # Extraire les données par type d'agent
        for step in plan.steps:
            result = step_results.get(step.step_id)
            if result is None or result.status != StepStatus.SUCCESS:
                continue

            data = result.data
            if data is None:
                continue

            if step.agent == AgentType.SQL_AGENT:
                self._collect_sql_data(response, step.step_id, data)
            elif step.agent == AgentType.ANALYSIS_AGENT:
                self._collect_analysis_data(response, step.step_id, data)

        logger.info(
            "Réponse agrégée — %d datasets, %d insights, %d viz, %d recos, "
            "partial=%s, failed=%d",
            len(response.data),
            len(response.insights),
            len(response.visualizations),
            len(response.recommendations),
            response.partial,
            len(failed),
        )
        return response

    # ─── Helpers ──────────────────────────────────────────────

    @staticmethod
    def _collect_sql_data(
        response: OrchestratorResponse,
        step_id: str,
        data: Any,
    ) -> None:
        """Collecte le DataFrame + méta depuis la sortie du SQL Agent."""
        if not isinstance(data, dict):
            logger.warning(
                "SQL Agent step %s : data n'est pas un dict (%s)",
                step_id,
                type(data).__name__,
            )
            return

        response.data.append(
            {
                "step_id": step_id,
                "records": data.get("records", []),
                "columns": data.get("columns", []),
                "row_count": data.get("row_count", 0),
                "sql": data.get("sql"),
            }
        )

    @staticmethod
    def _collect_analysis_data(
        response: OrchestratorResponse,
        step_id: str,
        data: Any,
    ) -> None:
        """Collecte insights, viz, recos depuis la sortie de l'Analyse Agent."""
        if not isinstance(data, dict):
            logger.warning(
                "Analyse Agent step %s : data n'est pas un dict (%s)",
                step_id,
                type(data).__name__,
            )
            return

        for insight in data.get("insights", []):
            if insight:
                response.insights.append(str(insight))

        for viz in data.get("visualizations", []):
            if viz:
                response.visualizations.append(viz)

        for reco in data.get("recommendations", []):
            if reco:
                response.recommendations.append(str(reco))
