"""
app/agents/analysis/stats/forecasting.py

Wrapper Prophet pour la task `forecasting`.

Principes
---------
- Pur : aucun side-effect (pas d'écriture KG, pas de log custom, pas de viz).
- JSON-safe : tous les outputs sont sérialisables (pas de DataFrame, pas de
  Timestamp, pas de np.float64 dans les listes retournées).
- Non-bloquant : les cas limites (série trop courte, gaps, NaN) sont
  signalés via `warnings`, jamais via une exception remontée à l'appelant.
- Aucun seuil métier hardcodé : les seuils de "faible/modérée/élevée
  incertitude" vivent dans le prompt LLM, pas ici.

Évaluation
----------
Une cross-validation temporelle (walk-forward) est exécutée après le fit
principal si la série est suffisamment longue (≥ _MIN_POINTS_FOR_CV).
Elle produit MAE, MAPE, RMSE sur un horizon réel — ces métriques sont
stockées dans `ForecastResult.evaluation` et transmises au LLM + KG.

Walk-forward (Prophet diagnostics)
-----------------------------------
  initial  = 70% de l'historique disponible
  period   = max(7, horizon_days // 2)     — fenêtre glissante
  horizon  = horizon_days jours            — même que le forecast demandé

Prophet génère N fenêtres de backtesting. On agrège MAE/MAPE/RMSE sur
toutes les fenêtres — chaque métrique est la moyenne sur toutes les
coupures (cutoffs). MAPE est sécurisé contre les valeurs nulles / proches
de zéro (division par un dénominateur clampé à 1e-9).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd


# ─── Constantes (pas des seuils métier — juste de la robustesse technique) ──

_MIN_POINTS_HARD = 10          # En dessous : pas de fit possible, on abandonne.
_MIN_POINTS_RECOMMENDED = 30   # En dessous : fit OK mais on warn.
_MIN_POINTS_FOR_CV = 60        # En dessous : cross-validation skippée (pas assez
                                #             de données pour des fenêtres stables).
_GAP_DAYS_WARN = 7             # Trou de plus de 7 jours consécutifs → warning.

_DEFAULT_MODEL = "prophet"


# ─── Dataclass de sortie ────────────────────────────────────────────────────


@dataclass
class ForecastEvaluation:
    """
    Métriques de qualité issues de la cross-validation temporelle.

    Attributes
    ----------
    mae : float
        Mean Absolute Error — erreur absolue moyenne en unités de la série.
    mape : float | None
        Mean Absolute Percentage Error en % — None si des zéros dans la série
        rendent le calcul non-représentatif.
    rmse : float
        Root Mean Squared Error — pénalise les grosses erreurs.
    horizon_days_evaluated : int
        Horizon (jours) sur lequel la CV a été faite — correspond au
        `horizon_days` demandé.
    n_cutoffs : int
        Nombre de fenêtres de backtesting utilisées.
    skipped : bool
        True si la CV n'a pas pu être exécutée (série trop courte, Prophet
        indisponible, ou erreur interne).
    skip_reason : str | None
        Explication textuelle du skip, à remonter dans les warnings.
    """

    mae: float = 0.0
    mape: Optional[float] = None
    rmse: float = 0.0
    horizon_days_evaluated: int = 0
    n_cutoffs: int = 0
    skipped: bool = False
    skip_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mae": self.mae,
            "mape": self.mape,
            "rmse": self.rmse,
            "horizon_days_evaluated": self.horizon_days_evaluated,
            "n_cutoffs": self.n_cutoffs,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
        }


@dataclass
class ForecastResult:
    """
    Résultat d'un forecast, JSON-safe et auto-suffisant.

    Attributes
    ----------
    historical : liste de {date: str ISO, value: float}
        Série historique nettoyée (ce qui a réellement servi au fit).
    forecast : liste de {date: str ISO, yhat: float, yhat_lower: float, yhat_upper: float}
        Projection future sur `horizon_days` jours.
    evaluation : ForecastEvaluation
        Métriques MAE / MAPE / RMSE issues de la cross-validation temporelle.
        `evaluation.skipped = True` si la CV n'a pas pu tourner.
    metadata : dict
        Inclut model, n_historical, horizon_days, last_date, first_forecast_date,
        last_forecast_date.
    diagnostics : dict
        Inclut trend_direction, seasonality_detected, mean_ci_width_pct,
        forecast_vs_history_change_pct.
    warnings : liste de str
        Garde-fous non bloquants (série courte, gaps, NaN drops, CV skippée, etc.).
    model : str
        Modèle utilisé. Présent aussi dans `metadata["model"]` pour faciliter
        la consommation côté KG / LLM.
    """

    historical: list[dict[str, Any]] = field(default_factory=list)
    forecast: list[dict[str, Any]] = field(default_factory=list)
    evaluation: ForecastEvaluation = field(default_factory=ForecastEvaluation)
    metadata: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    model: str = _DEFAULT_MODEL

    def is_empty(self) -> bool:
        """True si aucun forecast n'a pu être produit."""
        return not self.forecast

    def to_dict(self) -> dict[str, Any]:
        """Sérialisation complète pour le champ `stats` du TaskResult."""
        return {
            "model": self.model,
            "historical": self.historical,
            "forecast": self.forecast,
            "evaluation": self.evaluation.to_dict(),
            "metadata": self.metadata,
            "diagnostics": self.diagnostics,
            "warnings": list(self.warnings),
        }


# ─── API publique ───────────────────────────────────────────────────────────


def run_forecast(
    df: pd.DataFrame,
    value_col: str,
    date_col: str = "date",
    horizon_days: int = 30,
    *,
    model_name: str = _DEFAULT_MODEL,
) -> ForecastResult:
    """
    Fit + predict + cross-validation en une étape.

    Parameters
    ----------
    df : DataFrame en long format avec au minimum les colonnes `date_col` et `value_col`.
    value_col : nom de la colonne numérique à prédire (ex: "close_usd").
    date_col : nom de la colonne date (str ou datetime — converti automatiquement).
    horizon_days : nombre de jours à projeter dans le futur.
    model_name : pour l'instant uniquement "prophet" supporté. Hook Sprint 4.

    Returns
    -------
    ForecastResult — toujours retourné, même en cas d'échec partiel.
    Le champ `warnings` contient le détail des problèmes rencontrés.
    """
    result = ForecastResult(model=model_name)

    # ── 1. Validation structurelle (early-exit avec warnings, pas d'exception)
    if df is None or df.empty:
        result.warnings.append("DataFrame vide en entrée.")
        return result

    if value_col not in df.columns:
        result.warnings.append(
            f"Colonne valeur '{value_col}' absente du DataFrame "
            f"(colonnes disponibles : {list(df.columns)})."
        )
        return result

    if date_col not in df.columns:
        result.warnings.append(
            f"Colonne date '{date_col}' absente du DataFrame."
        )
        return result

    if horizon_days <= 0:
        result.warnings.append(
            f"horizon_days={horizon_days} invalide, doit être > 0."
        )
        return result

    # ── 2. Nettoyage : conversion types, drop NaN, tri, dédoublonnage
    work = df[[date_col, value_col]].copy()
    work.columns = ["ds", "y"]

    work["ds"] = pd.to_datetime(work["ds"], errors="coerce")
    work["y"] = pd.to_numeric(work["y"], errors="coerce")

    n_before = len(work)
    work = work.dropna(subset=["ds", "y"])
    n_dropped = n_before - len(work)
    if n_dropped > 0:
        result.warnings.append(
            f"{n_dropped} ligne(s) supprimée(s) (date ou valeur non parsable)."
        )

    # Dédoublonnage : si plusieurs valeurs pour la même date, on prend la moyenne.
    if work["ds"].duplicated().any():
        n_dups = int(work["ds"].duplicated().sum())
        work = work.groupby("ds", as_index=False)["y"].mean()
        result.warnings.append(
            f"{n_dups} date(s) dupliquée(s) — moyenne calculée."
        )

    work = work.sort_values("ds").reset_index(drop=True)
    n_historical = len(work)

    # ── 3. Garde-fous de taille
    if n_historical < _MIN_POINTS_HARD:
        result.warnings.append(
            f"Série trop courte ({n_historical} points < {_MIN_POINTS_HARD}) "
            f"— forecast abandonné."
        )
        return result

    if n_historical < _MIN_POINTS_RECOMMENDED:
        result.warnings.append(
            f"Série courte ({n_historical} points < {_MIN_POINTS_RECOMMENDED} "
            f"recommandés) — fiabilité réduite."
        )

    # ── 4. Détection des gaps
    if n_historical >= 2:
        diffs = work["ds"].diff().dt.days.dropna()
        max_gap = int(diffs.max()) if not diffs.empty else 0
        if max_gap > _GAP_DAYS_WARN:
            result.warnings.append(
                f"Trou maximal de {max_gap} jours détecté dans la série "
                f"(> {_GAP_DAYS_WARN} jours)."
            )

    # ── 5. Import Prophet (retardé : lourd à charger)
    try:
        from prophet import Prophet
    except ImportError as e:
        result.warnings.append(
            f"Prophet non installé ({e}) — forecast impossible."
        )
        return result

    # ── 6. Fit Prophet principal
    # Saisonnalités : on laisse Prophet décider (auto) sauf daily qu'on
    # désactive systématiquement (données journalières → pas pertinent).
    try:
        prophet_model = Prophet(
            daily_seasonality=False,
            weekly_seasonality="auto",
            yearly_seasonality="auto",
            interval_width=0.80,
        )
        prophet_model.fit(work)
    except Exception as e:
        result.warnings.append(f"Échec du fit Prophet : {e}")
        return result

    # ── 7. Predict (horizon futur uniquement)
    try:
        future = prophet_model.make_future_dataframe(
            periods=horizon_days,
            freq="D",
            include_history=False,
        )
        pred = prophet_model.predict(future)
    except Exception as e:
        result.warnings.append(f"Échec de la prédiction Prophet : {e}")
        return result

    # ── 8. Cross-validation temporelle (walk-forward backtesting)
    evaluation, cv_warnings = _run_cross_validation(
        work=work,
        horizon_days=horizon_days,
        n_historical=n_historical,
    )
    result.evaluation = evaluation
    result.warnings.extend(cv_warnings)

    # ── 9. Sérialisation JSON-safe
    result.historical = _serialize_historical(work)
    result.forecast = _serialize_forecast(pred)

    last_date = work["ds"].iloc[-1]
    first_forecast_date = pred["ds"].iloc[0]
    last_forecast_date = pred["ds"].iloc[-1]

    result.metadata = {
        "model": model_name,
        "n_historical": n_historical,
        "horizon_days": horizon_days,
        "last_date": last_date.date().isoformat(),
        "first_forecast_date": first_forecast_date.date().isoformat(),
        "last_forecast_date": last_forecast_date.date().isoformat(),
        "value_col": value_col,
    }

    result.diagnostics = _compute_diagnostics(work, pred, prophet_model)

    return result


# ─── Cross-validation temporelle ────────────────────────────────────────────


def _run_cross_validation(
    work: pd.DataFrame,
    horizon_days: int,
    n_historical: int,
) -> tuple[ForecastEvaluation, list[str]]:
    """
    Walk-forward backtesting via prophet.diagnostics.

    Paramètres calculés dynamiquement depuis la taille de la série :
      initial  = 70% de l'historique (en jours calendaires)
      period   = max(7, horizon_days // 2)   — pas de glissement
      horizon  = horizon_days jours

    Returns
    -------
    (ForecastEvaluation, warnings)
    """
    warnings: list[str] = []

    if n_historical < _MIN_POINTS_FOR_CV:
        return ForecastEvaluation(
            skipped=True,
            skip_reason=(
                f"Série trop courte pour la cross-validation "
                f"({n_historical} points < {_MIN_POINTS_FOR_CV} requis)."
            ),
        ), [
            f"Cross-validation skippée : {n_historical} points disponibles "
            f"(minimum {_MIN_POINTS_FOR_CV})."
        ]

    try:
        from prophet.diagnostics import cross_validation, performance_metrics
    except ImportError as e:
        return ForecastEvaluation(
            skipped=True,
            skip_reason=f"prophet.diagnostics non disponible : {e}",
        ), [f"Cross-validation skippée : prophet.diagnostics non disponible ({e})."]

    # ── Calcul des paramètres de CV
    # On travaille en jours calendaires réels (pas en nombre de points)
    # pour rester cohérent avec la fréquence daily de Prophet.
    total_days = int((work["ds"].iloc[-1] - work["ds"].iloc[0]).days)
    initial_days = max(int(total_days * 0.70), horizon_days * 2)
    period_days = max(7, horizon_days // 2)
    horizon_str = f"{horizon_days} days"
    initial_str = f"{initial_days} days"
    period_str = f"{period_days} days"

    # Garde-fou : initial doit laisser de la place pour au moins 1 cutoff.
    remaining_days = total_days - initial_days
    if remaining_days < horizon_days:
        return ForecastEvaluation(
            skipped=True,
            skip_reason=(
                f"Fenêtre résiduelle ({remaining_days} jours) insuffisante "
                f"pour 1 cutoff d'horizon {horizon_days} jours."
            ),
        ), [
            f"Cross-validation skippée : pas assez d'historique résiduel "
            f"après initial={initial_days}j pour horizon={horizon_days}j."
        ]

    # ── Fit du modèle de CV (Prophet doit être refitté sur work)
    try:
        from prophet import Prophet

        cv_model = Prophet(
            daily_seasonality=False,
            weekly_seasonality="auto",
            yearly_seasonality="auto",
            interval_width=0.80,
        )
        cv_model.fit(work)
    except Exception as e:
        return ForecastEvaluation(
            skipped=True,
            skip_reason=f"Échec fit Prophet pour CV : {e}",
        ), [f"Cross-validation skippée : échec du fit ({e})."]

    # ── Cross-validation walk-forward
    try:
        df_cv = cross_validation(
            cv_model,
            initial=initial_str,
            period=period_str,
            horizon=horizon_str,
            parallel=None,  # pas de multiprocessing (environnement contraint)
        )
    except Exception as e:
        return ForecastEvaluation(
            skipped=True,
            skip_reason=f"Erreur cross_validation Prophet : {e}",
        ), [f"Cross-validation échouée : {e}."]

    if df_cv is None or df_cv.empty:
        return ForecastEvaluation(
            skipped=True,
            skip_reason="cross_validation a retourné un DataFrame vide.",
        ), ["Cross-validation : aucun résultat produit."]

    n_cutoffs = int(df_cv["cutoff"].nunique())

    # ── Calcul des métriques agrégées sur toutes les fenêtres
    try:
        df_perf = performance_metrics(df_cv, rolling_window=1.0)
        # rolling_window=1.0 → une seule ligne agrégée sur toutes les coupures
        mae = float(df_perf["mae"].mean())
        rmse = float(df_perf["rmse"].mean())

        # MAPE : calculé manuellement pour éviter les divisions par zéro.
        # La lib prophet peut lever si les valeurs réelles contiennent des zéros.
        mape = _safe_mape(df_cv)

    except Exception as e:
        warnings.append(f"Erreur lors du calcul des métriques CV : {e}.")
        return ForecastEvaluation(
            skipped=True,
            skip_reason=f"Calcul métriques échoué : {e}",
            n_cutoffs=n_cutoffs,
        ), warnings

    return ForecastEvaluation(
        mae=round(mae, 6),
        mape=round(mape, 4) if mape is not None else None,
        rmse=round(rmse, 6),
        horizon_days_evaluated=horizon_days,
        n_cutoffs=n_cutoffs,
        skipped=False,
    ), warnings


def _safe_mape(df_cv: pd.DataFrame) -> Optional[float]:
    """
    Calcule le MAPE manuellement à partir du DataFrame de cross-validation.

    MAPE = mean(|y - yhat| / max(|y|, epsilon)) * 100

    Retourne None si trop de valeurs réelles sont nulles ou proches de zéro
    (résultat non-représentatif).
    """
    if "y" not in df_cv.columns or "yhat" not in df_cv.columns:
        return None

    y = df_cv["y"].to_numpy(dtype=float)
    yhat = df_cv["yhat"].to_numpy(dtype=float)

    abs_y = np.abs(y)
    # Si plus de 10% des valeurs sont proches de zéro → MAPE non fiable
    near_zero_ratio = float(np.mean(abs_y < 1e-6))
    if near_zero_ratio > 0.10:
        return None

    denom = np.where(abs_y < 1e-9, 1e-9, abs_y)
    mape = float(np.mean(np.abs(y - yhat) / denom) * 100.0)
    return mape


# ─── Sérialisation ──────────────────────────────────────────────────────────


def _serialize_historical(work: pd.DataFrame) -> list[dict[str, Any]]:
    """work has columns ds (Timestamp), y (float)."""
    return [
        {"date": row.ds.date().isoformat(), "value": float(row.y)}
        for row in work.itertuples(index=False)
    ]


def _serialize_forecast(pred: pd.DataFrame) -> list[dict[str, Any]]:
    """pred has Prophet's standard output: ds, yhat, yhat_lower, yhat_upper."""
    return [
        {
            "date": row.ds.date().isoformat(),
            "yhat": float(row.yhat),
            "yhat_lower": float(row.yhat_lower),
            "yhat_upper": float(row.yhat_upper),
        }
        for row in pred[["ds", "yhat", "yhat_lower", "yhat_upper"]].itertuples(
            index=False
        )
    ]


# ─── Diagnostics (pour le LLM) ──────────────────────────────────────────────


def _compute_diagnostics(
    work: pd.DataFrame,
    pred: pd.DataFrame,
    prophet_model: Any,
) -> dict[str, Any]:
    """
    Calcule des indicateurs synthétiques que le LLM citera dans ses insights.

    Aucun seuil de qualification n'est appliqué ici — on retourne des
    valeurs brutes (direction, pourcentages, booléens) et c'est le prompt
    qui dit comment les interpréter.
    """
    diagnostics: dict[str, Any] = {}

    # ── Direction de la tendance projetée
    yhat = pred["yhat"].to_numpy()
    if len(yhat) >= 2:
        first, last = float(yhat[0]), float(yhat[-1])
        denom = abs(first) if abs(first) > 1e-9 else 1.0
        slope_pct = (last - first) / denom * 100.0
        diagnostics["trend_slope_pct"] = round(slope_pct, 4)
        if abs(slope_pct) < 1.0:
            diagnostics["trend_direction"] = "flat"
        elif slope_pct > 0:
            diagnostics["trend_direction"] = "upward"
        else:
            diagnostics["trend_direction"] = "downward"
    else:
        diagnostics["trend_direction"] = "flat"
        diagnostics["trend_slope_pct"] = 0.0

    # ── Saisonnalités détectées (booléens d'après le modèle)
    seas: dict[str, bool] = {}
    try:
        for comp in ("weekly", "yearly"):
            seas[comp] = bool(prophet_model.seasonalities.get(comp))
    except Exception:
        seas = {"weekly": False, "yearly": False}
    diagnostics["seasonality_detected"] = seas

    # ── Largeur moyenne de l'IC en % du yhat (proxy d'incertitude)
    if len(pred) > 0:
        widths = (pred["yhat_upper"] - pred["yhat_lower"]).abs()
        yhat_abs = pred["yhat"].abs().replace(0, np.nan)
        ratios = (widths / yhat_abs).dropna()
        if not ratios.empty:
            diagnostics["mean_ci_width_pct"] = round(float(ratios.mean() * 100), 2)
        else:
            diagnostics["mean_ci_width_pct"] = None
    else:
        diagnostics["mean_ci_width_pct"] = None

    # ── Variation projetée vs dernière valeur historique
    last_hist = float(work["y"].iloc[-1])
    last_pred = float(pred["yhat"].iloc[-1]) if len(pred) else last_hist
    denom = abs(last_hist) if abs(last_hist) > 1e-9 else 1.0
    diagnostics["forecast_vs_history_change_pct"] = round(
        (last_pred - last_hist) / denom * 100.0, 4
    )

    # ── Valeurs clés citables par le LLM
    if len(pred) > 0:
        diagnostics["first_forecast_value"] = round(float(pred["yhat"].iloc[0]), 6)
        diagnostics["last_forecast_value"] = round(float(pred["yhat"].iloc[-1]), 6)
        diagnostics["last_historical_value"] = round(last_hist, 6)

    return diagnostics