"""
app/agents/analysis/tasks/hybrid_summary.py
Task : synthèse hybride à partir d'un DataFrame interne + payload Tavily.

Activée quand :
  - Intent = AGGREGATION (ou autres analytiques) ET
  - SemanticContext.analytic_gaps non vide ET
  - Tavily a été consulté pour combler les gaps.

Le planner construit un step Analysis avec task='hybrid_summary' et inclut :
  - input_steps = ["sql_1"] : le DataFrame interne arrive via upstream
  - tavily_payload : le contenu externe (sources + extracted_content)
  - analytic_gaps : la liste des termes qui ont déclenché Tavily

Cette task assemble un résumé NL qui distingue clairement :
  - les chiffres / faits provenant de la base interne
  - les compléments / définitions provenant des sources externes citées

Sortie : un TaskResult standard. Pas de viz pour cette task (le DataFrame
peut être complexe, on ne fait pas de hypothèses sur la forme dans le MVP).
Si tu veux une viz dans un ticket futur, il suffira d'appeler les helpers
de descriptive sur le DataFrame.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import pandas as pd

from app.agents.analysis.llm.insight_generator import (
    GeneratedInsights,
    InsightGenerator,
)
from app.agents.analysis.stats.descriptive import (
    detect_dataframe_shape,
    summarize_groupby,
    summarize_numeric,
    summarize_timeseries,
)
from app.agents.analysis.tasks.base import (
    AnalysisTask,
    TaskResult,
    register_task,
)
from app.agents.analysis.tasks.external_summary import (
    MAX_EXTRACTED_CONTENT_CHARS,
    MAX_SOURCES_FOR_LLM,
    _trim_extracted,
    _trim_sources,
    _validate_payload,
)

logger = logging.getLogger(__name__)


EMPTY_RESULT_CONFIDENCE = 0.25


# ─── Helpers ───────────────────────────────────────────────────────────────


def _summarize_dataframe(
    df: pd.DataFrame,
) -> tuple[dict[str, Any], str | None, list[str]]:
    """
    Calcule des stats compactes sur le DataFrame, sans hypothèses fortes
    sur les noms de colonnes. On reroute selon la shape détectée.

    Returns:
        (stats, subtype, warnings)
    """
    warnings: list[str] = []
    shape_info = detect_dataframe_shape(df)
    shape = shape_info["shape"]

    if shape == "empty":
        warnings.append("hybrid_summary: DataFrame interne vide")
        return {}, "empty", warnings

    datetime_cols = shape_info.get("datetime_cols", [])
    numeric_cols = shape_info.get("numeric_cols", [])
    categorical_cols = shape_info.get("categorical_cols", [])

    if shape == "timeseries" and datetime_cols and numeric_cols:
        try:
            stats = summarize_timeseries(
                df=df,
                date_col=datetime_cols[0],
                value_col=numeric_cols[0],
            )
            return stats, "timeseries", warnings
        except Exception as e:  # noqa: BLE001
            warnings.append(f"hybrid_summary: échec timeseries summary: {e}")
            return {}, "timeseries_failed", warnings

    if shape == "groupby" and categorical_cols and numeric_cols:
        try:
            stats = summarize_groupby(
                df=df,
                group_col=categorical_cols[0],
                value_col=numeric_cols[0],
            )
            return stats, "groupby", warnings
        except Exception as e:  # noqa: BLE001
            warnings.append(f"hybrid_summary: échec groupby summary: {e}")
            return {}, "groupby_failed", warnings

    if shape == "numeric_only" and numeric_cols:
        try:
            stats = summarize_numeric(df[numeric_cols[0]])
            return stats, "numeric_only", warnings
        except Exception as e:  # noqa: BLE001
            warnings.append(f"hybrid_summary: échec numeric summary: {e}")
            return {}, "numeric_failed", warnings

    warnings.append(
        f"hybrid_summary: shape '{shape}' non supportée pour la synthèse"
    )
    return {}, shape, warnings


def _extract_dataframe_from_upstream(
    instruction: dict[str, Any],
    upstream_results: dict[str, Any] | None,
) -> tuple[pd.DataFrame, list[str]]:
    """
    Le runner convertit normalement les records en DataFrame avant d'appeler
    la task. Pour hybrid_summary, on s'appuie sur le DataFrame déjà fourni
    en argument `df` de run() — donc cette fonction n'est pas utilisée.

    Conservée ici si, plus tard, on veut un mode où la task lit elle-même
    plusieurs upstream (ex: cross-table). Pour le MVP hybrid : `df` suffit.
    """
    return pd.DataFrame(), []


# ─── La task ──────────────────────────────────────────────────────────────


@register_task
class HybridSummaryTask(AnalysisTask):
    """
    Task qui produit un résumé NL combinant :
      - le DataFrame interne (passé en argument df par le runner)
      - le payload Tavily passé via instruction['tavily_payload']
    """

    task_name = "hybrid_summary"

    def __init__(self, insight_generator: InsightGenerator | None = None) -> None:
        self._insight_generator: InsightGenerator | None = insight_generator

    def set_insight_generator(self, generator: InsightGenerator) -> None:
        self._insight_generator = generator

    def run(
        self,
        df: pd.DataFrame,
        instruction: dict[str, Any],
        semantic_context: dict[str, Any] | None = None,
        **_unused: Any,
    ) -> TaskResult:
        start_time = time.perf_counter()
        warnings: list[str] = []

        # 1. Stats sur le DataFrame interne.
        df_stats, df_subtype, df_warnings = _summarize_dataframe(df)
        warnings.extend(df_warnings)

        # 2. Validation du payload Tavily.
        payload = instruction.get("tavily_payload")
        is_valid, err = _validate_payload(payload)
        if not is_valid:
            warnings.append(f"hybrid_summary: {err}")
            payload = None

        sources_trimmed: list[dict[str, Any]] = []
        extracted_trimmed: str = ""
        query: str = ""
        if payload is not None and isinstance(payload, dict):
            sources_trimmed = _trim_sources(payload.get("sources") or [])
            extracted_trimmed = _trim_extracted(payload.get("extracted_content"))
            query = str(payload.get("query") or "").strip()

        analytic_gaps = instruction.get("analytic_gaps") or []
        if not isinstance(analytic_gaps, list):
            analytic_gaps = []

        # 3. Si on n'a NI stats internes utiles NI payload Tavily, on retourne
        #    un résultat vide signalé.
        if not df_stats and not sources_trimmed and not extracted_trimmed:
            warnings.append(
                "hybrid_summary: aucune donnée exploitable (DataFrame vide ET "
                "payload Tavily vide)"
            )
            return self._build_empty_result(
                start_time=start_time,
                warnings=warnings,
                sources=sources_trimmed,
                df_subtype=df_subtype,
            )

        # 4. Génération NL.
        generated = self._generate_insights(
            df_stats=df_stats,
            df_subtype=df_subtype,
            query=query,
            sources=sources_trimmed,
            extracted_content=extracted_trimmed,
            analytic_gaps=analytic_gaps,
            semantic_context=semantic_context,
            warnings_so_far=warnings,
        )
        warnings.extend(generated.warnings)

        # 5. Assemblage TaskResult.
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        metadata: dict[str, Any] = {
            "task": self.task_name,
            "subtype": df_subtype or "hybrid",
            "confidence": generated.overall_confidence,
            "n_rows": int(len(df)),
            "method": "tavily_extract+internal",
            "duration_ms": duration_ms,
            "fallback_used": generated.used_fallback,
            "sources": sources_trimmed,
            "tavily_query": query,
            "analytic_gaps": list(analytic_gaps),
        }
        if generated.llm_metadata:
            metadata["llm"] = generated.llm_metadata

        return TaskResult(
            insights=[i.text for i in generated.insights],
            visualizations=[],
            recommendations=[r.text for r in generated.recommendations],
            stats=df_stats,
            metadata=metadata,
            warnings=warnings,
            kg_payload=[],
        )

    # ─── Internal helpers ─────────────────────────────────────────────────

    def _generate_insights(
        self,
        *,
        df_stats: dict[str, Any],
        df_subtype: str | None,
        query: str,
        sources: list[dict[str, Any]],
        extracted_content: str,
        analytic_gaps: list[str],
        semantic_context: dict[str, Any] | None,
        warnings_so_far: list[str],
    ) -> GeneratedInsights:
        if self._insight_generator is None:
            from app.agents.analysis.llm.schemas import Insight

            warning = (
                "InsightGenerator non injecté dans HybridSummaryTask. "
                "Aucun insight NL généré."
            )
            logger.warning(warning)
            return GeneratedInsights(
                insights=[
                    Insight(
                        text=(
                            "Données internes et sources externes disponibles, "
                            "synthèse non générée (LLM indisponible)."
                        ),
                        confidence=EMPTY_RESULT_CONFIDENCE,
                        supporting_stats=[],
                    )
                ],
                recommendations=[],
                overall_confidence=EMPTY_RESULT_CONFIDENCE,
                warnings=[warning],
                llm_metadata={"fallback": True, "no_generator": True},
                used_fallback=True,
            )

        # Stats passées au LLM = stats internes + métadonnées Tavily
        # (le LLM peut citer "n_sources", "n", "trend_direction" etc).
        merged_stats = {
            **df_stats,
            "n_sources": len(sources),
            "extracted_chars": len(extracted_content),
            "n_analytic_gaps": len(analytic_gaps),
        }

        return self._insight_generator.generate(
            task_name=self.task_name,
            stats=merged_stats,
            prompt_kwargs={
                "df_stats": df_stats,
                "df_subtype": df_subtype,
                "query": query,
                "sources": sources,
                "extracted_content": extracted_content,
                "analytic_gaps": analytic_gaps,
                "semantic_hints": semantic_context,
                "warnings": list(warnings_so_far),
            },
        )

    def _build_empty_result(
        self,
        *,
        start_time: float,
        warnings: list[str],
        sources: list[dict[str, Any]],
        df_subtype: str | None,
    ) -> TaskResult:
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return TaskResult(
            insights=[],
            visualizations=[],
            recommendations=[],
            stats={},
            metadata={
                "task": self.task_name,
                "subtype": df_subtype or "empty",
                "confidence": EMPTY_RESULT_CONFIDENCE,
                "n_rows": 0,
                "method": "tavily_extract+internal",
                "duration_ms": duration_ms,
                "fallback_used": False,
                "sources": sources,
            },
            warnings=warnings,
            kg_payload=[],
        )
