"""
app/agents/analysis/viz/forecast_chart.py

Template Plotly dédié au forecast.

Composition
-----------
- Trace 1 : ligne historique (couleur primaire du theme)
- Trace 2 : ligne forecast (style pointillé, couleur d'accent)
- Trace 3 : borne supérieure de l'IC (invisible, sert d'ancre pour le fill)
- Trace 4 : borne inférieure de l'IC avec fill='tonexty' → bande translucide
- Ligne verticale annotée séparant historique / futur

Cohérence avec line_chart.py
----------------------------
Même signature `(df, config) -> plotly_dict`. Même theme. Même registry.
"""

from __future__ import annotations

from typing import Any, TypedDict

import pandas as pd

from app.agents.analysis.viz.templates import (
    register_default_for_shape,
    register_viz,
)
from app.agents.analysis.viz.theme import (
    apply_theme_to_layout,
    get_series_color,
    get_theme,
)


# ─── Config TypedDict (documente le contrat d'entrée) ───────────────────────


class ForecastChartConfig(TypedDict, total=False):
    """
    Configuration acceptée par `build_forecast_chart`.

    Champs
    ------
    title : titre du graphique.
    y_label : libellé de l'axe Y (ex: "Prix USD").
    historical_name : libellé de la trace historique (défaut "Historique").
    forecast_name : libellé de la trace forecast (défaut "Prévision").
    ci_label : libellé de la bande de confiance (défaut "Intervalle de confiance").
    last_historical_date : date ISO marquant la frontière historique/forecast.
        Si fournie, une ligne verticale est ajoutée à cette date.
    """

    title: str
    y_label: str
    historical_name: str
    forecast_name: str
    ci_label: str
    last_historical_date: str


# ─── Builder ────────────────────────────────────────────────────────────────


@register_viz("forecast_chart")
@register_default_for_shape("forecast_series")
def build_forecast_chart(
    df: pd.DataFrame,
    config: ForecastChartConfig | None = None,
) -> dict[str, Any]:
    """
    Construit un Plotly dict à partir d'un DataFrame combiné historique + forecast.

    Le DataFrame doit contenir :
      - `date` : date ISO (str) ou datetime
      - `segment` : "historical" ou "forecast"
      - `value` : float (pour les lignes historique ET forecast — `yhat` pour le forecast)
      - `yhat_lower`, `yhat_upper` : float, présents pour les lignes "forecast"

    Returns
    -------
    Plotly figure dict (data + layout) prêt à être sérialisé en JSON.
    """
    cfg: ForecastChartConfig = config or {}
    theme = get_theme()

    # Historique : couleur primaire (1re série). Forecast : couleur dédiée
    # `forecast` du theme — pensée pour ça, ce qui évite de cycler sur la
    # palette catégorielle et garantit une distinction visuelle stable.
    color_hist = get_series_color(0, theme=theme)
    color_fcst = theme["colors"].get("forecast") or get_series_color(1, theme=theme)

    # ── Validation : structure minimale
    if df is None or df.empty or "segment" not in df.columns:
        empty_layout = {
            "title": cfg.get("title", "Prévision"),
            "annotations": [
                {
                    "text": "Aucune donnée à afficher.",
                    "xref": "paper",
                    "yref": "paper",
                    "x": 0.5,
                    "y": 0.5,
                    "showarrow": False,
                }
            ],
        }
        return {
            "data": [],
            "layout": apply_theme_to_layout(empty_layout, theme=theme),
        }

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")

    hist = df[df["segment"] == "historical"]
    fcst = df[df["segment"] == "forecast"]

    traces: list[dict[str, Any]] = []

    # ── Trace historique
    if not hist.empty:
        traces.append(
            {
                "type": "scatter",
                "mode": "lines",
                "name": cfg.get("historical_name", "Historique"),
                "x": [d.isoformat() for d in hist["date"]],
                "y": [float(v) for v in hist["value"]],
                "line": {"color": color_hist, "width": 2},
                "hovertemplate": "%{x|%Y-%m-%d}<br>%{y:.4f}<extra></extra>",
            }
        )

    # ── Bande de confiance (deux traces : upper invisible, lower avec fill)
    if not fcst.empty and "yhat_upper" in fcst.columns and "yhat_lower" in fcst.columns:
        x_fcst = [d.isoformat() for d in fcst["date"]]
        # Upper bound — invisible, sert d'ancre pour le fill suivant
        traces.append(
            {
                "type": "scatter",
                "mode": "lines",
                "name": cfg.get("ci_label", "Intervalle de confiance"),
                "x": x_fcst,
                "y": [float(v) for v in fcst["yhat_upper"]],
                "line": {"width": 0},
                "showlegend": False,
                "hoverinfo": "skip",
            }
        )
        # Lower bound — fill vers la trace précédente (l'upper)
        traces.append(
            {
                "type": "scatter",
                "mode": "lines",
                "name": cfg.get("ci_label", "Intervalle de confiance"),
                "x": x_fcst,
                "y": [float(v) for v in fcst["yhat_lower"]],
                "line": {"width": 0},
                "fill": "tonexty",
                "fillcolor": _to_rgba(color_fcst, alpha=0.2),
                "hovertemplate": (
                    "IC: %{customdata[0]:.4f} – %{y:.4f}<extra></extra>"
                ),
                "customdata": [[float(v)] for v in fcst["yhat_upper"]],
            }
        )

    # ── Trace forecast (par-dessus la bande)
    if not fcst.empty:
        traces.append(
            {
                "type": "scatter",
                "mode": "lines",
                "name": cfg.get("forecast_name", "Prévision"),
                "x": [d.isoformat() for d in fcst["date"]],
                "y": [float(v) for v in fcst["value"]],
                "line": {"color": color_fcst, "width": 2, "dash": "dash"},
                "hovertemplate": "%{x|%Y-%m-%d}<br>%{y:.4f}<extra></extra>",
            }
        )

    # ── Ligne verticale séparant historique / forecast
    shapes: list[dict[str, Any]] = []
    annotations: list[dict[str, Any]] = []
    boundary = cfg.get("last_historical_date")
    # Fallback : si non fourni, on déduit du DataFrame.
    if boundary is None and not hist.empty:
        boundary = hist["date"].iloc[-1].date().isoformat()

    if boundary:
        shapes.append(
            {
                "type": "line",
                "xref": "x",
                "yref": "paper",
                "x0": boundary,
                "x1": boundary,
                "y0": 0,
                "y1": 1,
                "line": {"color": "#888", "width": 1, "dash": "dot"},
            }
        )
        annotations.append(
            {
                "x": boundary,
                "y": 1.02,
                "xref": "x",
                "yref": "paper",
                "text": "Début prévision",
                "showarrow": False,
                "font": {"size": 10, "color": "#666"},
                "xanchor": "left",
            }
        )

    layout: dict[str, Any] = {
        "title": cfg.get("title", "Prévision"),
        "xaxis": {"title": "Date"},
        "yaxis": {"title": cfg.get("y_label", "Valeur")},
        "hovermode": "x unified",
        "shapes": shapes,
        "annotations": annotations,
        "legend": {"orientation": "h", "yanchor": "bottom", "y": -0.25},
    }

    return {
        "data": traces,
        "layout": apply_theme_to_layout(layout, theme=theme),
    }


# ─── Helpers ────────────────────────────────────────────────────────────────


def _to_rgba(color: str, alpha: float) -> str:
    """
    Convertit une couleur (hex `#rrggbb` ou rgb()) en rgba() avec alpha donné.
    Robuste aux formats du theme.
    """
    color = color.strip()

    # Hex #rrggbb
    if color.startswith("#") and len(color) == 7:
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        return f"rgba({r},{g},{b},{alpha})"

    # rgb(r,g,b) → rgba(r,g,b,a)
    if color.lower().startswith("rgb(") and color.endswith(")"):
        inside = color[4:-1]
        return f"rgba({inside},{alpha})"

    # rgba(...) déjà — on remplace l'alpha en fin
    if color.lower().startswith("rgba(") and color.endswith(")"):
        parts = color[5:-1].split(",")
        if len(parts) >= 3:
            return f"rgba({parts[0].strip()},{parts[1].strip()},{parts[2].strip()},{alpha})"

    # Fallback : gris translucide neutre
    return f"rgba(120,120,120,{alpha})"