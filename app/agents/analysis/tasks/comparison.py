"""
Comparison analysis task.

Consumes two or more SQL upstream results and compares their numeric series
after aligning them by date. The common production case is BTC vs ETH volume
over a recent period.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar

import pandas as pd

from app.agents.analysis.llm.insight_generator import InsightGenerator
from app.agents.analysis.tasks.base import AnalysisTask, TaskResult, register_task
from app.agents.analysis.viz.line_chart import line_chart


DEFAULT_DATE_COL = "date"
DEFAULT_VALUE_CANDIDATES = (
    "volume",
    "total_volume",
    "close_usd",
    "close",
    "value",
    "price",
)


@register_task
class ComparisonTask(AnalysisTask):
    """Compare numeric time series from multiple upstream SQL steps."""

    task_name: ClassVar[str] = "comparison"
    consumes_multiple_steps: ClassVar[bool] = True

    def __init__(self, insight_generator: InsightGenerator | None = None) -> None:
        self._insight_generator: InsightGenerator | None = insight_generator

    def set_insight_generator(self, generator: InsightGenerator) -> None:
        """Injecté par le runner. La logique déterministe reste le fallback."""
        self._insight_generator = generator

    def run(
        self,
        df: pd.DataFrame | None,
        instruction: dict[str, Any],
        semantic_context: dict[str, Any] | None = None,
        upstream_results: dict[str, Any] | None = None,
        **_unused: Any,
    ) -> TaskResult:
        warnings: list[str] = []
        upstream_results = upstream_results or {}
        semantic_context = semantic_context or {}
        input_steps = instruction.get("input_steps") or list(upstream_results.keys())
        date_col = str(instruction.get("date_col") or DEFAULT_DATE_COL)
        requested_value_col = instruction.get("value_col")

        if not input_steps:
            return _warning_result(
                "Aucun input_step fourni : comparaison impossible.",
                warnings=warnings,
            )
        if len(input_steps) < 2:
            return _warning_result(
                "La comparaison nécessite au moins deux input_steps exploitables.",
                warnings=warnings,
            )

        series_frames: list[pd.DataFrame] = []
        symbol_by_step: dict[str, str] = {}
        row_counts: dict[str, int] = {}

        for step_id in input_steps:
            step_out = upstream_results.get(step_id)
            if step_out is None:
                warnings.append(
                    f"Dataset upstream manquant pour '{step_id}' : comparaison partielle."
                )
                continue

            step_df = _step_to_dataframe(step_out)
            if step_df is None:
                warnings.append(
                    f"Input step '{step_id}' malformé : attendu dict avec records ou list."
                )
                continue
            if step_df.empty:
                warnings.append(
                    f"Dataset upstream vide pour '{step_id}' : aucun record à comparer."
                )
                continue
            if date_col not in step_df.columns:
                warnings.append(
                    f"Input step '{step_id}' invalide : colonne date '{date_col}' absente."
                )
                continue

            value_col = _pick_value_column(
                step_df,
                requested_value_col=requested_value_col,
                date_col=date_col,
            )
            if value_col is None:
                warnings.append(
                    f"Métrique non détectée pour '{step_id}' : aucune colonne numérique exploitable."
                )
                continue

            symbol = _series_name(step_id, step_df)
            if symbol in symbol_by_step.values():
                symbol = f"{symbol}_{step_id}"
                warnings.append(
                    f"Nom de série dupliqué pour '{step_id}', renommage en '{symbol}'."
                )
            symbol_by_step[step_id] = symbol

            cleaned = step_df[[date_col, value_col]].copy()
            cleaned[date_col] = pd.to_datetime(cleaned[date_col], errors="coerce")
            cleaned[value_col] = pd.to_numeric(cleaned[value_col], errors="coerce")
            cleaned = cleaned.dropna(subset=[date_col, value_col])
            if cleaned.empty:
                warnings.append(
                    f"Input step '{step_id}' inexploitable après conversion date/valeur."
                )
                continue

            if cleaned[date_col].duplicated().any():
                warnings.append(
                    f"Input step '{step_id}' contient des dates dupliquées ; "
                    "moyenne des valeurs par date."
                )
                cleaned = cleaned.groupby(date_col, as_index=False)[value_col].mean()

            cleaned = cleaned.rename(columns={value_col: symbol})
            series_frames.append(cleaned)
            row_counts[step_id] = len(cleaned)

        if len(series_frames) < 2:
            return _warning_result(
                "Moins de deux séries valides après lecture des upstream results.",
                warnings=warnings,
                metadata={"input_steps": input_steps, "row_counts": row_counts},
            )

        aligned = series_frames[0]
        for next_df in series_frames[1:]:
            aligned = aligned.merge(next_df, on=date_col, how="inner")
        aligned = aligned.sort_values(date_col).reset_index(drop=True)

        if aligned.empty:
            return _warning_result(
                "Aucune date commune entre les séries : comparaison impossible.",
                warnings=warnings,
                metadata={"input_steps": input_steps, "row_counts": row_counts},
            )

        series_cols = [col for col in aligned.columns if col != date_col]
        if len(aligned) < 2:
            warnings.append(
                "Pas assez de points alignés pour une comparaison robuste "
                f"(n={len(aligned)}, minimum recommandé=2)."
            )
        stats = _compute_comparison_stats(aligned, series_cols, date_col)
        fallback_insights = _build_insights(stats, series_cols)
        fallback_recommendations = _build_recommendations(stats)
        insights = fallback_insights
        recommendations = fallback_recommendations
        llm_confidence: float | None = None
        generated = self._generate_insights(
            stats=stats,
            question=_extract_question(instruction, semantic_context),
            symbols=series_cols,
            metric=str(stats.get("metric") or requested_value_col or ""),
            time_period=_extract_time_period(semantic_context, stats),
            warnings_so_far=warnings,
        )
        if generated is not None:
            warnings.extend(generated.warnings)
            if (
                not generated.used_fallback
                and generated.insights
            ):
                insights = [insight.text for insight in generated.insights]
                recommendations = [
                    recommendation.text
                    for recommendation in generated.recommendations
                ] or fallback_recommendations
                llm_confidence = generated.overall_confidence
        visualizations = _build_visualizations(aligned, series_cols, date_col, warnings)

        metadata = {
            "task": self.task_name,
            "subtype": "volume_timeseries",
            "confidence": (
                llm_confidence
                if llm_confidence is not None
                else (0.9 if visualizations else 0.75)
            ),
            "n_rows": int(len(aligned)),
            "method": "date_aligned_numeric_comparison",
            "input_steps": input_steps,
            "series_by_step": symbol_by_step,
            "row_counts": row_counts,
        }

        return TaskResult(
            insights=insights,
            visualizations=visualizations,
            recommendations=recommendations,
            stats=stats,
            metadata=metadata,
            warnings=warnings,
            kg_payload=[],
        )

    def _generate_insights(
        self,
        *,
        stats: dict[str, Any],
        question: str,
        symbols: list[str],
        metric: str,
        time_period: str,
        warnings_so_far: list[str],
    ) -> Any:
        if self._insight_generator is None:
            return None
        return self._insight_generator.generate(
            task_name=self.task_name,
            stats=stats,
            prompt_kwargs={
                "question": question,
                "symbols": symbols,
                "metric": metric,
                "time_period": time_period,
                "comparison_stats": stats,
                "warnings": list(warnings_so_far),
            },
        )


def _step_to_dataframe(step_out: Any) -> pd.DataFrame | None:
    if isinstance(step_out, dict):
        records = step_out.get("records")
        columns = step_out.get("columns") or []
    elif isinstance(step_out, list):
        records = step_out
        columns = []
    else:
        return None

    if not records:
        return pd.DataFrame(columns=columns)
    try:
        df = pd.DataFrame.from_records(records)
    except Exception:  # noqa: BLE001
        return None
    if columns:
        ordered = [col for col in columns if col in df.columns]
        extras = [col for col in df.columns if col not in ordered]
        df = df[ordered + extras]
    return df


def _pick_value_column(
    df: pd.DataFrame,
    *,
    requested_value_col: Any,
    date_col: str,
) -> str | None:
    if isinstance(requested_value_col, str) and requested_value_col in df.columns:
        return requested_value_col

    numeric_cols: list[str] = []
    for col in df.columns:
        if col == date_col:
            continue
        converted = pd.to_numeric(df[col], errors="coerce")
        if converted.notna().any():
            numeric_cols.append(col)
    if not numeric_cols:
        return None

    for candidate in DEFAULT_VALUE_CANDIDATES:
        if candidate in numeric_cols:
            return candidate
    return numeric_cols[0]


def _series_name(step_id: str, df: pd.DataFrame) -> str:
    for col in ("symbol", "ticker", "entity", "asset"):
        if col not in df.columns:
            continue
        values = df[col].dropna().astype(str).unique()
        if len(values) == 1 and values[0].strip():
            return values[0].strip()
    return step_id


def _extract_question(
    instruction: dict[str, Any],
    semantic_context: dict[str, Any],
) -> str:
    for source in (instruction, semantic_context):
        for key in ("question", "raw_question", "corrected_question"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _extract_time_period(
    semantic_context: dict[str, Any],
    stats: dict[str, Any],
) -> str:
    time_filters = semantic_context.get("time_filters") or []
    if time_filters:
        return ", ".join(str(item) for item in time_filters)
    start = stats.get("start_date")
    end = stats.get("end_date")
    if start and end:
        return f"du {start} au {end}"
    return ""


def _compute_comparison_stats(
    aligned: pd.DataFrame,
    series_cols: list[str],
    date_col: str,
) -> dict[str, Any]:
    metrics_by_symbol: dict[str, dict[str, float]] = {}
    means: dict[str, float] = {}
    totals: dict[str, float] = {}

    for col in series_cols:
        series = pd.to_numeric(aligned[col], errors="coerce").dropna()
        metrics = {
            "mean_volume": _round(series.mean()),
            "median_volume": _round(series.median()),
            "min_volume": _round(series.min()),
            "max_volume": _round(series.max()),
            "total_volume": _round(series.sum()),
        }
        metrics_by_symbol[col] = metrics
        means[col] = metrics["mean_volume"]
        totals[col] = metrics["total_volume"]

    leader = max(totals, key=totals.get)
    ordered = sorted(means.items(), key=lambda item: item[1], reverse=True)
    average_volume_pct_diff = None
    if len(ordered) >= 2 and ordered[1][1] != 0:
        average_volume_pct_diff = _round(((ordered[0][1] - ordered[1][1]) / ordered[1][1]) * 100)

    return {
        "metric": "volume",
        "date_col": date_col,
        "start_date": aligned[date_col].min().date().isoformat(),
        "end_date": aligned[date_col].max().date().isoformat(),
        "aligned_points": int(len(aligned)),
        "symbols": series_cols,
        "metrics_by_symbol": metrics_by_symbol,
        "average_volume_pct_diff": average_volume_pct_diff,
        "higher_volume_asset": leader,
    }


def _build_insights(stats: dict[str, Any], series_cols: list[str]) -> list[str]:
    leader = stats["higher_volume_asset"]
    metrics = stats["metrics_by_symbol"]
    period = f"du {stats['start_date']} au {stats['end_date']}"
    insights = [
        (
            f"Sur la période alignée {period}, {leader} affiche le volume moyen "
            f"le plus élevé."
        )
    ]

    if len(series_cols) >= 2:
        first, second = series_cols[0], series_cols[1]
        diff = stats.get("average_volume_pct_diff")
        if diff is not None:
            other = second if leader == first else first
            insights.append(
                f"L'écart entre les volumes moyens est d'environ {diff:.2f}% "
                f"en faveur de {leader} par rapport à {other}."
            )

    for symbol in series_cols:
        symbol_stats = metrics[symbol]
        insights.append(
            f"{symbol} : volume moyen {symbol_stats['mean_volume']:,.2f}, "
            f"médiane {symbol_stats['median_volume']:,.2f}, "
            f"total {symbol_stats['total_volume']:,.2f}."
        )

    return insights


def _build_recommendations(stats: dict[str, Any]) -> list[str]:
    leader = stats["higher_volume_asset"]
    return [
        (
            f"Surveiller {leader} en priorité pour les signaux de liquidité, "
            "car son volume domine la période comparée."
        ),
        (
            "Vérifier les dates où les courbes divergent fortement afin "
            "d'identifier les changements de participation du marché."
        ),
        (
            "Comparer les volumes normalisés en USD pour mesurer la liquidité "
            "économique réelle."
        ),
    ]


def _build_visualizations(
    aligned: pd.DataFrame,
    series_cols: list[str],
    date_col: str,
    warnings: list[str],
) -> list[dict[str, Any]]:
    try:
        fig = line_chart(
            aligned,
            {
                "x_col": date_col,
                "y_cols": series_cols,
                "title": "Comparaison des volumes dans le temps",
                "x_label": "Date",
                "y_label": "Volume",
                "mode": "lines",
                "series_labels": {col: col for col in series_cols},
            },
        )
        return [fig]
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Visualisation comparison impossible : {type(exc).__name__}: {exc}")
        return []


def _warning_result(
    message: str,
    *,
    warnings: list[str],
    metadata: dict[str, Any] | None = None,
) -> TaskResult:
    all_warnings = [*warnings, message]
    return TaskResult(
        insights=[message],
        visualizations=[],
        recommendations=[],
        stats={},
        metadata={
            "task": "comparison",
            "subtype": "empty",
            "confidence": 0.0,
            "n_rows": 0,
            **(metadata or {}),
        },
        warnings=all_warnings,
    )


def _round(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return round(number, 6)
