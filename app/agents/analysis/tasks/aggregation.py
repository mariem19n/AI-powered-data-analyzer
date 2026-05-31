"""Aggregation task: concise answers for single aggregate questions."""

from __future__ import annotations

import logging
import math
import time
from typing import Any

import pandas as pd

from app.agents.analysis.llm.insight_generator import GeneratedInsights, InsightGenerator
from app.agents.analysis.llm.schemas import Insight
from app.agents.analysis.tasks.base import AnalysisTask, TaskResult, register_task
from app.agents.analysis.viz.templates import get_viz

logger = logging.getLogger(__name__)

_AGGREGATE_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("average", ("moyen", "moyenne", "average", "avg", "mean")),
    ("sum", ("somme", "total", "sum", "volume total")),
    ("min", ("minimum", "min", "plus bas")),
    ("max", ("maximum", "max", "plus haut")),
    ("count", ("combien", "nombre", "count", "how many")),
)


def _infer_aggregate_type(question: str, instruction: dict[str, Any]) -> str:
    explicit = instruction.get("aggregate_type")
    if isinstance(explicit, str) and explicit:
        return explicit.lower()
    lowered = question.lower()
    for aggregate_type, aliases in _AGGREGATE_ALIASES:
        if any(alias in lowered for alias in aliases):
            return aggregate_type
    return "average"


def _find_date_col(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            return str(col)
        if str(col).lower() in {"date", "day", "timestamp", "created_at"}:
            parsed = pd.to_datetime(df[col], errors="coerce")
            if parsed.notna().any():
                df[col] = parsed
                return str(col)
    return None


def _select_value_col(
    df: pd.DataFrame,
    aggregate_type: str,
    semantic_context: dict[str, Any] | None,
    instruction: dict[str, Any],
) -> str | None:
    requested = instruction.get("value_col")
    if isinstance(requested, str) and requested in df.columns:
        return requested

    numeric_cols = [
        str(col)
        for col in df.columns
        if pd.api.types.is_numeric_dtype(df[col])
    ]
    if not numeric_cols:
        return None

    prefixes = {
        "average": ("avg", "average", "mean", "moyenne"),
        "sum": ("sum", "total"),
        "min": ("min", "minimum"),
        "max": ("max", "maximum"),
        "count": ("count", "nombre", "n", "row_count"),
    }.get(aggregate_type, ())
    for col in numeric_cols:
        lowered = col.lower()
        if any(lowered.startswith(prefix) or prefix in lowered for prefix in prefixes):
            return col

    metric_names = []
    for metric in (semantic_context or {}).get("metrics", []) or []:
        if isinstance(metric, dict):
            metric_names.extend(
                str(metric.get(key) or "").lower()
                for key in ("name", "formula", "description")
            )
    for col in numeric_cols:
        lowered = col.lower()
        if any(name and (name in lowered or lowered in name) for name in metric_names):
            return col

    return numeric_cols[0]


def _semantic_question(semantic_context: dict[str, Any] | None) -> str:
    if not semantic_context:
        return ""
    return str(
        semantic_context.get("raw_question")
        or semantic_context.get("corrected_question")
        or ""
    )


def _entity_label(semantic_context: dict[str, Any] | None) -> str | None:
    entities = (semantic_context or {}).get("entity_filters") or []
    if entities and isinstance(entities[0], dict):
        return str(
            entities[0].get("entity_name")
            or entities[0].get("label")
            or entities[0].get("value")
            or ""
        ) or None
    return None


def _metric_label(
    value_col: str | None,
    semantic_context: dict[str, Any] | None,
    question: str = "",
) -> str:
    metrics = (semantic_context or {}).get("metrics") or []
    if metrics and isinstance(metrics[0], dict):
        raw_metric = str(metrics[0].get("name") or metrics[0].get("description") or "")
    else:
        raw_metric = value_col or "valeur"

    text = f"{raw_metric} {value_col or ''} {question}".lower()
    entity = _entity_label(semantic_context)
    if "bitcoin" in question.lower() and not entity:
        entity = "Bitcoin"

    if any(token in text for token in ("prix", "price", "close_usd", "close")):
        return f"prix de {entity}" if entity else "prix"
    if "volume" in text:
        return f"volume de {entity}" if entity else "volume"
    return raw_metric or "valeur"


def _unit_for(metric: str, value_col: str | None) -> str | None:
    text = f"{metric} {value_col or ''}".lower()
    if "usd" in text or "prix" in text or "price" in text or "close" in text:
        return "USD"
    return None


def _compute_aggregate(
    df: pd.DataFrame,
    aggregate_type: str,
    value_col: str | None,
) -> float | int | None:
    if aggregate_type == "count":
        if value_col:
            series = pd.to_numeric(df[value_col], errors="coerce")
            if len(df) == 1 and not series.dropna().empty:
                return int(series.dropna().iloc[0])
        return int(len(df))
    if not value_col:
        return None
    series = pd.to_numeric(df[value_col], errors="coerce").dropna()
    if series.empty:
        return None
    if len(df) == 1 and aggregate_type in value_col.lower():
        return float(series.iloc[0])
    if aggregate_type == "average":
        return float(series.mean())
    if aggregate_type == "sum":
        return float(series.sum())
    if aggregate_type == "min":
        return float(series.min())
    if aggregate_type == "max":
        return float(series.max())
    return float(series.mean())


def _observation_count(df: pd.DataFrame, value_col: str | None) -> int:
    """Return the source observation count when SQL exposes it."""
    count_columns = {
        "row_count",
        "record_count",
        "observation_count",
        "observations",
        "count",
        "n",
    }
    for col in df.columns:
        col_name = str(col)
        if value_col and col_name == value_col:
            continue
        lowered = col_name.lower()
        if lowered in count_columns or lowered.endswith("_count"):
            values = pd.to_numeric(df[col], errors="coerce").dropna()
            if len(df) == 1 and not values.empty:
                return int(values.iloc[0])
    return int(len(df))


def _format_number(value: float | int | None, unit: str | None = None) -> str:
    if value is None:
        return "non disponible"
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return "non disponible"
    decimals = 0 if float(value).is_integer() else 2
    formatted = f"{float(value):,.{decimals}f}".replace(",", " ").replace(".", ",")
    return f"{formatted} {unit}" if unit else formatted


def _period_from_df(
    df: pd.DataFrame,
    date_col: str | None,
    semantic_context: dict[str, Any] | None,
) -> tuple[str | None, str | None, str]:
    lowered_cols = {str(col).lower(): col for col in df.columns}
    start_col = lowered_cols.get("start_date") or lowered_cols.get("period_start")
    end_col = lowered_cols.get("end_date") or lowered_cols.get("period_end")
    if start_col is not None and end_col is not None:
        starts = pd.to_datetime(df[start_col], errors="coerce").dropna()
        ends = pd.to_datetime(df[end_col], errors="coerce").dropna()
        if not starts.empty and not ends.empty:
            start = starts.min().date().isoformat()
            end = ends.max().date().isoformat()
            return start, end, f"entre le {start} et le {end}"

    if date_col and date_col in df.columns:
        dates = pd.to_datetime(df[date_col], errors="coerce").dropna()
        if not dates.empty:
            start = dates.min().date().isoformat()
            end = dates.max().date().isoformat()
            return start, end, f"entre le {start} et le {end}"

    filters = (semantic_context or {}).get("time_filters") or []
    labels = []
    for item in filters:
        if isinstance(item, dict):
            labels.append(str(item.get("raw_text") or item.get("expression") or item.get("filter_clause") or ""))
    label = ", ".join([item for item in labels if item]) or "la période demandée"
    return None, None, label


def _build_stats(
    df: pd.DataFrame,
    instruction: dict[str, Any],
    semantic_context: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    question = str(instruction.get("question") or _semantic_question(semantic_context))
    aggregate_type = _infer_aggregate_type(question, instruction)
    date_col = _find_date_col(df)
    value_col = _select_value_col(df, aggregate_type, semantic_context, instruction)
    metric = str(instruction.get("metric") or _metric_label(value_col, semantic_context, question))
    unit = instruction.get("unit") or _unit_for(metric, value_col)
    value = _compute_aggregate(df, aggregate_type, value_col)
    start_date, end_date, period_label = _period_from_df(df, date_col, semantic_context)

    if value is None:
        warnings.append("Aucune valeur numérique exploitable pour l'agrégation.")

    row_count = (
        int(value)
        if aggregate_type == "count" and value is not None
        else _observation_count(df, value_col)
    )
    stats = {
        "analysis_type": "aggregation",
        "question": question,
        "aggregate_type": aggregate_type,
        "metric": metric,
        "aggregate_value": value,
        "formatted_value": _format_number(value, str(unit) if unit else None),
        "unit": unit,
        "row_count": row_count,
        "start_date": start_date,
        "end_date": end_date,
        "time_period": period_label,
        "date_col": date_col,
        "value_col": value_col,
        "calculation_method": _calculation_method(aggregate_type, metric),
    }
    if value_col and value_col in df.columns:
        series = pd.to_numeric(df[value_col], errors="coerce").dropna()
        if not series.empty:
            stats["min"] = float(series.min())
            stats["max"] = float(series.max())
    return stats, warnings


def _calculation_method(aggregate_type: str, metric: str) -> str:
    labels = {
        "average": f"moyenne de {metric}",
        "sum": f"somme de {metric}",
        "min": f"minimum de {metric}",
        "max": f"maximum de {metric}",
        "count": "nombre d'observations",
    }
    return labels.get(aggregate_type, f"agrégation {aggregate_type} de {metric}")


def _fallback_generated(stats: dict[str, Any], warnings: list[str]) -> GeneratedInsights:
    aggregate_type = stats.get("aggregate_type")
    metric = stats.get("metric") or "valeur"
    formatted = stats.get("formatted_value") or "non disponible"
    period = stats.get("time_period") or "la période demandée"
    row_count = stats.get("row_count", 0)
    direct = _direct_answer(aggregate_type, metric, formatted, period)
    insights = [
        Insight(text=direct, confidence=0.86, supporting_stats=["aggregate_value", "time_period"]),
        Insight(
            text=f"Cette valeur correspond à {stats.get('calculation_method')} sur la période analysée.",
            confidence=0.82,
            supporting_stats=["calculation_method"],
        ),
        Insight(
            text=f"Le calcul repose sur {row_count} observation(s) utilisée(s).",
            confidence=0.78,
            supporting_stats=["row_count"],
        ),
    ]
    return GeneratedInsights(
        insights=insights,
        recommendations=[],
        overall_confidence=0.82,
        warnings=warnings,
        llm_metadata={"fallback": True},
        used_fallback=True,
    )


def _direct_answer(
    aggregate_type: str | None,
    metric: str,
    formatted: str,
    period: str,
) -> str:
    if aggregate_type == "average":
        return f"La moyenne de {metric} sur {period} est de {formatted}."
    if aggregate_type == "sum":
        return f"Le total de {metric} sur {period} est de {formatted}."
    if aggregate_type == "min":
        return f"Le minimum de {metric} sur {period} est de {formatted}."
    if aggregate_type == "max":
        return f"Le maximum de {metric} sur {period} est de {formatted}."
    if aggregate_type == "count":
        return f"Le nombre d'observations sur {period} est de {formatted}."
    return f"La valeur agrégée de {metric} sur {period} est de {formatted}."


def _aggregate_label(aggregate_type: str | None) -> str:
    labels = {
        "average": "Moyenne",
        "sum": "Total",
        "min": "Minimum",
        "max": "Maximum",
        "count": "Nombre",
    }
    return labels.get(str(aggregate_type), "Valeur agrégée")


def _build_visualizations(
    df: pd.DataFrame,
    stats: dict[str, Any],
    instruction: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Build the aggregation visualization through the viz registry."""
    warnings: list[str] = []
    value_col = stats.get("value_col")
    if not isinstance(value_col, str) or value_col not in df.columns:
        warnings.append(
            "Visualisation d'agrégation non générée : colonne de valeur absente."
        )
        return [], warnings
    if stats.get("aggregate_value") is None:
        warnings.append(
            "Visualisation d'agrégation non générée : valeur agrégée absente."
        )
        return [], warnings

    try:
        viz_fn = get_viz("aggregation_summary")
        aggregate_label = _aggregate_label(str(stats.get("aggregate_type") or ""))
        config = {
            "value_col": value_col,
            "date_col": stats.get("date_col"),
            "aggregate_value": stats.get("aggregate_value"),
            "formatted_value": stats.get("formatted_value"),
            "aggregate_label": aggregate_label,
            "metric": stats.get("metric") or value_col,
            "unit": stats.get("unit"),
            "title": instruction.get("title")
            or f"{aggregate_label} - {stats.get('metric') or value_col}",
            "x_label": instruction.get("x_label") or "Date",
            "y_label": instruction.get("y_label") or stats.get("metric") or value_col,
        }
        return [viz_fn(df, config)], warnings
    except Exception as exc:  # noqa: BLE001 - viz should not break analysis
        logger.exception("Échec génération viz 'aggregation_summary'")
        warnings.append(
            "Échec génération viz 'aggregation_summary' : "
            f"{type(exc).__name__}: {exc}"
        )
        return [], warnings


@register_task
class AggregationTask(AnalysisTask):
    """Answer pure aggregation questions without descriptive trend analysis."""

    task_name = "aggregation"

    def __init__(self, insight_generator: InsightGenerator | None = None) -> None:
        self._insight_generator = insight_generator

    def set_insight_generator(self, generator: InsightGenerator) -> None:
        """Inject the shared InsightGenerator used by the analysis runner."""
        self._insight_generator = generator

    def run(
        self,
        df: pd.DataFrame,
        instruction: dict[str, Any],
        semantic_context: dict[str, Any] | None = None,
        **_: Any,
    ) -> TaskResult:
        start = time.perf_counter()
        warnings: list[str] = []
        if df is None or df.empty:
            warnings.append("DataFrame vide — aucune agrégation possible.")
            return TaskResult(
                insights=[],
                visualizations=[],
                recommendations=[],
                stats={},
                metadata={
                    "task": self.task_name,
                    "subtype": "empty",
                    "confidence": 0.2,
                    "n_rows": 0,
                    "method": "Agrégation SQL",
                    "duration_ms": int((time.perf_counter() - start) * 1000),
                    "fallback_used": False,
                },
                warnings=warnings,
            )

        stats, stat_warnings = _build_stats(df, instruction, semantic_context)
        warnings.extend(stat_warnings)
        visualizations, viz_warnings = _build_visualizations(df, stats, instruction)
        warnings.extend(viz_warnings)
        generated = self._generate(stats=stats, warnings=warnings)
        duration_ms = int((time.perf_counter() - start) * 1000)

        return TaskResult(
            insights=[insight.text for insight in generated.insights[:3]],
            visualizations=visualizations,
            recommendations=[rec.text for rec in generated.recommendations[:1]],
            stats=stats,
            metadata={
                "task": self.task_name,
                "subtype": stats.get("aggregate_type"),
                "confidence": generated.overall_confidence,
                "n_rows": stats.get("row_count", len(df)),
                "method": "Agrégation SQL",
                "duration_ms": duration_ms,
                "fallback_used": generated.used_fallback,
            },
            warnings=warnings + generated.warnings,
            kg_payload=[],
        )

    def _generate(self, *, stats: dict[str, Any], warnings: list[str]) -> GeneratedInsights:
        if self._insight_generator is None:
            return _fallback_generated(stats, warnings)
        return self._insight_generator.generate(
            task_name=self.task_name,
            stats=stats,
            prompt_kwargs={
                "question": stats.get("question", ""),
                "requested_metric": stats.get("metric", ""),
                "aggregate_type": stats.get("aggregate_type", ""),
                "aggregate_value": stats.get("aggregate_value"),
                "time_period": stats.get("time_period", ""),
                "row_count": stats.get("row_count", 0),
                "sql_result_summary": stats,
                "unit": stats.get("unit"),
                "warnings": warnings,
            },
        )
