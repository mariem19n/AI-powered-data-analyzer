"""
app/agents/analysis/tasks/forecasting.py

Task `forecasting` : projection temporelle d'une série numérique via Prophet.

Caractéristiques
----------------
- consumes_multiple_steps = False : un seul step SQL upstream attendu, déjà
  converti en DataFrame par le runner et passé en `df=`.
- L'horizon est paramétrable via `instruction["horizon_days"]` (défaut 30).
- La colonne valeur est détectée via `semantic_context["columns"]`, avec
  fallback : première colonne numérique non-date du DataFrame.
- Appelle l'InsightGenerator (1 seul appel LLM) pour produire les insights
  en français.
- Aucune écriture KG depuis la task. `kg_payload` reste vide pour l'instant
  (hook reporté à un sprint ultérieur).

Conventions respectées
----------------------
- Aucun nom de colonne / domaine hardcodé : tout passe par le SemanticContext
  ou par auto-détection sur dtype.
- Toute condition fragile → warning non-bloquant, jamais d'exception qui
  remonte vers le runner.
- La task ne fait AUCUNE écriture (KG, DB, cache). Elle produit uniquement
  un TaskResult.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

import pandas as pd

from app.agents.analysis.llm.insight_generator import InsightGenerator
from app.agents.analysis.stats.forecasting import (
    ForecastResult,
    run_forecast,
)
from app.agents.analysis.tasks.base import (
    AnalysisTask,
    TaskResult,
    register_task,
)
from app.agents.analysis.viz.forecast_chart import build_forecast_chart

logger = logging.getLogger(__name__)


# ─── Constantes paramétrables ──────────────────────────────────────────────


_DEFAULT_HORIZON_DAYS: int = 30
_DATE_COL_CANDIDATES: tuple[str, ...] = ("date", "dt", "datetime", "timestamp", "day")


# ─── Task ──────────────────────────────────────────────────────────────────


@register_task
class ForecastingTask(AnalysisTask):
    """
    Forecast Prophet sur une série temporelle univariée.
    """

    task_name: ClassVar[str] = "forecasting"
    consumes_multiple_steps: ClassVar[bool] = False

    def __init__(
        self, insight_generator: InsightGenerator | None = None
    ) -> None:
        self._insight_generator: InsightGenerator | None = insight_generator

    def set_insight_generator(self, generator: InsightGenerator) -> None:
        """Injecté par le runner au démarrage. Optionnel pour les tests."""
        self._insight_generator = generator

    # ── API publique : run() ─────────────────────────────────────────────

    def run(
        self,
        df: pd.DataFrame | None,
        instruction: dict[str, Any],
        semantic_context: dict[str, Any] | None = None,
        upstream_results: dict[str, Any] | None = None,
        **_unused: Any,
    ) -> TaskResult:
        warnings: list[str] = []
        semantic_context = semantic_context or {}

        # 1. Validation du DataFrame upstream.
        if df is None or df.empty:
            warnings.append("Aucune donnée upstream à forecaster.")
            return self._build_empty_result(warnings=warnings)

        # 2. Détection de la colonne date.
        date_col, date_warnings = self._detect_date_column(df)
        warnings.extend(date_warnings)
        if date_col is None:
            warnings.append("Aucune colonne date détectable dans le DataFrame.")
            return self._build_empty_result(warnings=warnings)

        # 3. Détection de la colonne valeur.
        value_col, val_warnings = self._detect_value_column(
            df, date_col, semantic_context
        )
        warnings.extend(val_warnings)
        if value_col is None:
            warnings.append(
                "Aucune colonne numérique détectable pour le forecast."
            )
            return self._build_empty_result(warnings=warnings)

        # 4. Résolution des paramètres.
        horizon = self._resolve_horizon(instruction, warnings)
        series_label = self._resolve_series_label(df, value_col, semantic_context)

        # 5. Forecast.
        forecast_result = run_forecast(
            df=df,
            value_col=value_col,
            date_col=date_col,
            horizon_days=horizon,
        )
        warnings.extend(forecast_result.warnings)

        stats_dict = forecast_result.to_dict()

        # Si forecast vide (série trop courte / Prophet absent / etc.) → on
        # arrête là proprement, avec les warnings remontés.
        if forecast_result.is_empty():
            metadata: dict[str, Any] = {
                "task": self.task_name,
                "subtype": "forecasting",
                "method": forecast_result.model,
                "n_rows": int(len(df)),
                "confidence": 0.0,
                "horizon_days": horizon,
                "value_col": value_col,
                "date_col": date_col,
                "series_label": series_label,
            }
            return TaskResult(
                insights=[],
                visualizations=[],
                recommendations=[],
                stats=stats_dict,
                metadata=metadata,
                kg_payload=[],
                warnings=warnings,
            )

        # 6. Visualisation.
        viz = self._build_viz(
            forecast_result=forecast_result,
            series_label=series_label,
            value_col=value_col,
            warnings=warnings,
        )
        visualizations: list[dict[str, Any]] = [viz] if viz else []

        # 7. Insights LLM.
        generated = self._generate_insights(
            stats=stats_dict,
            series_label=series_label,
            semantic_context=semantic_context,
            warnings_so_far=warnings,
        )

        insights_text: list[str] = []
        recommendations_text: list[str] = []
        overall_confidence: float | None = None

        if generated is not None:
            insights_text = [i.text for i in generated.insights]
            recommendations_text = [r.text for r in generated.recommendations]
            overall_confidence = generated.overall_confidence
            warnings.extend(generated.warnings)
        else:
            warnings.append(
                "Génération d'insights indisponible : aucun texte produit."
            )

        # 8. Metadata structurée.
        n_historical = forecast_result.metadata.get("n_historical") or len(df)
        metadata = {
            "task": self.task_name,
            "subtype": "forecasting",
            "method": forecast_result.model,
            "n_rows": int(n_historical),
            "confidence": float(overall_confidence) if overall_confidence is not None else 0.5,
            "horizon_days": horizon,
            "value_col": value_col,
            "date_col": date_col,
            "series_label": series_label,
            "last_historical_date": forecast_result.metadata.get("last_date"),
            "first_forecast_date": forecast_result.metadata.get("first_forecast_date"),
            "last_forecast_date": forecast_result.metadata.get("last_forecast_date"),
        }

        # 9. KG payload : volontairement vide pour l'instant (hook KG reporté).
        kg_payload: list[dict[str, Any]] = []

        return TaskResult(
            insights=insights_text,
            visualizations=visualizations,
            recommendations=recommendations_text,
            stats=stats_dict,
            metadata=metadata,
            kg_payload=kg_payload,
            warnings=warnings,
        )

    # ── Helpers internes ─────────────────────────────────────────────────

    def _generate_insights(
        self,
        *,
        stats: dict[str, Any],
        series_label: str,
        semantic_context: dict[str, Any] | None,
        warnings_so_far: list[str],
    ) -> Any:
        """
        Appelle l'InsightGenerator s'il est injecté. Retourne None sinon.
        """
        if self._insight_generator is None:
            return None
        return self._insight_generator.generate(
            task_name=self.task_name,
            stats=stats,
            prompt_kwargs={
                "series_label": series_label,
                "semantic_hints": semantic_context,
                "warnings": list(warnings_so_far),
            },
        )

    def _build_empty_result(self, *, warnings: list[str]) -> TaskResult:
        """Résultat vide cohérent quand la task ne peut rien produire."""
        return TaskResult(
            insights=[],
            visualizations=[],
            recommendations=[],
            stats={},
            metadata={
                "task": self.task_name,
                "subtype": "forecasting",
                "confidence": 0.0,
                "n_rows": 0,
                "empty": True,
            },
            kg_payload=[],
            warnings=warnings,
        )

    def _detect_date_column(
        self, df: pd.DataFrame
    ) -> tuple[str | None, list[str]]:
        """Cherche une colonne date par nom puis par parsing."""
        for cand in _DATE_COL_CANDIDATES:
            if cand in df.columns:
                return cand, []

        # Dernier recours : la première colonne qui parse en datetime.
        for col in df.columns:
            try:
                parsed = pd.to_datetime(df[col], errors="coerce")
            except Exception:
                continue
            if parsed.notna().sum() >= max(2, int(0.8 * len(df))):
                return col, [f"Colonne date détectée par parsing : '{col}'."]

        return None, []

    def _detect_value_column(
        self,
        df: pd.DataFrame,
        date_col: str,
        semantic_context: dict[str, Any] | None,
    ) -> tuple[str | None, list[str]]:
        """
        Priorité au semantic_context (colonne explicitement résolue par le
        Semantic Layer), fallback sur la première colonne numérique.
        """
        warnings: list[str] = []

        if semantic_context:
            cols = semantic_context.get("columns") or []
            for entry in cols:
                if not isinstance(entry, dict):
                    continue
                cname = entry.get("column") or entry.get("name")
                if cname and cname in df.columns and cname != date_col:
                    if pd.api.types.is_numeric_dtype(df[cname]):
                        return cname, []
                    warnings.append(
                        f"Colonne '{cname}' du semantic_context non-numérique, "
                        f"fallback automatique."
                    )

        numeric_cols = [
            c
            for c in df.columns
            if c != date_col and pd.api.types.is_numeric_dtype(df[c])
        ]
        if numeric_cols:
            warnings.append(
                f"Colonne valeur déduite automatiquement : '{numeric_cols[0]}'."
            )
            return numeric_cols[0], warnings

        return None, warnings

    def _resolve_horizon(
        self, instruction: dict[str, Any], warnings: list[str]
    ) -> int:
        raw = instruction.get("horizon_days", _DEFAULT_HORIZON_DAYS)
        try:
            h = int(raw)
            if h <= 0:
                raise ValueError
            return h
        except (TypeError, ValueError):
            warnings.append(
                f"horizon_days={raw!r} invalide, fallback sur "
                f"{_DEFAULT_HORIZON_DAYS} jours."
            )
            return _DEFAULT_HORIZON_DAYS

    def _resolve_series_label(
        self,
        df: pd.DataFrame,
        value_col: str,
        semantic_context: dict[str, Any] | None,
    ) -> str:
        """
        Libellé humain pour la série forecastée.
        Ex: "BTC close_usd" si symbol présent, sinon juste value_col.
        """
        if "symbol" in df.columns:
            symbols = df["symbol"].dropna().unique().tolist()
            if len(symbols) == 1:
                return f"{symbols[0]} {value_col}"

        if semantic_context:
            entities = semantic_context.get("entities") or []
            if len(entities) == 1:
                ent = entities[0]
                if isinstance(ent, dict):
                    name = ent.get("canonical") or ent.get("name")
                    if name:
                        return f"{name} {value_col}"

        return value_col

    def _build_viz(
        self,
        *,
        forecast_result: ForecastResult,
        series_label: str,
        value_col: str,
        warnings: list[str],
    ) -> dict[str, Any] | None:
        combined = self._build_combined_df(forecast_result)
        if combined.empty:
            return None

        last_hist_date = forecast_result.metadata.get("last_date")

        config = {
            "title": f"Prévision — {series_label}",
            "y_label": value_col,
            "historical_name": "Historique",
            "forecast_name": "Prévision",
            "ci_label": "Intervalle de confiance",
            "last_historical_date": last_hist_date,
        }

        try:
            return build_forecast_chart(combined, config)
        except Exception as e:
            warnings.append(f"Échec de la construction du forecast_chart : {e}")
            return None

    @staticmethod
    def _build_combined_df(forecast_result: ForecastResult) -> pd.DataFrame:
        """
        Concatène historical + forecast en un DataFrame unique au format
        attendu par `forecast_chart`.
        """
        hist = pd.DataFrame(forecast_result.historical or [])
        fcst = pd.DataFrame(forecast_result.forecast or [])

        if hist.empty and fcst.empty:
            return pd.DataFrame(
                columns=["date", "segment", "value", "yhat_lower", "yhat_upper"]
            )

        if not hist.empty:
            hist["segment"] = "historical"
            hist["yhat_lower"] = None
            hist["yhat_upper"] = None

        if not fcst.empty:
            fcst = fcst.rename(columns={"yhat": "value"})
            fcst["segment"] = "forecast"

        return pd.concat([hist, fcst], ignore_index=True, sort=False)[
            ["date", "segment", "value", "yhat_lower", "yhat_upper"]
        ]