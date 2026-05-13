"""
app/agents/analysis/viz/scatter_plot.py

Visualisation : scatter plot 2D pour anomalies multivariées.

S'enregistre sous le nom "scatter".
PAS de @register_default_for_shape : ce n'est pas un défaut auto pour
une shape de DataFrame. La task anomaly_detection l'invoque explicitement
quand exactement 2 colonnes sont analysées par Isolation Forest.

Caractéristiques :
- Affiche tous les points (normaux + anomalies) en 2D.
- Les anomalies sont mises en évidence avec une couleur distincte
  (rouge anomalie depuis le thème) et une taille plus grande.
- Aucun nom de colonne ni couleur en dur : tout vient de la config
  ou du thème.

Le retour est un dict Plotly JSON-serializable (équivalent fig.to_dict()).
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

import pandas as pd
import plotly.graph_objects as go

from app.agents.analysis.viz.templates import register_viz
from app.agents.analysis.viz.theme import (
    apply_theme_to_layout,
    get_series_color,
    get_theme,
)

logger = logging.getLogger(__name__)


# ─── Contrat de configuration ─────────────────────────────────────────────


class AnomalyMarker(TypedDict, total=False):
    """
    Indicateur d'anomalie sur un scatter 2D.

    Chaque anomalie a deux coordonnées (x, y) correspondant aux deux
    colonnes analysées. Optionnellement une date pour le tooltip.
    """

    x: float                # valeur sur l'axe X
    y: float                # valeur sur l'axe Y
    label: str              # libellé optionnel (ex: date)
    score: float            # score d'anomalie (Isolation Forest)


class ScatterPlotConfig(TypedDict, total=False):
    """
    Configuration d'un scatter plot 2D.

    Champs requis :
    - x_col : nom de la colonne pour l'axe X
    - y_col : nom de la colonne pour l'axe Y

    Champs optionnels :
    - title, x_label, y_label : texte du titre et libellés des axes
    - anomaly_markers : list[AnomalyMarker] — points à mettre en
                        évidence (typiquement les anomalies détectées)
    - normal_label : libellé pour les points normaux (défaut : "Données")
    - anomaly_label : libellé pour les anomalies (défaut : "Anomalies")
    - hover_col : nom d'une colonne supplémentaire à afficher dans
                  le tooltip (typiquement la date)
    - theme_overrides : dict d'overrides pour get_theme()
    - layout_overrides : dict de layout Plotly appliqué en dernier
    """

    x_col: str
    y_col: str
    title: str
    x_label: str
    y_label: str
    anomaly_markers: list[AnomalyMarker]
    normal_label: str
    anomaly_label: str
    hover_col: str
    theme_overrides: dict[str, Any]
    layout_overrides: dict[str, Any]


# ─── Fonction principale ──────────────────────────────────────────────────


@register_viz("scatter")
def render_scatter_plot(
    df: pd.DataFrame,
    config: dict[str, Any],
) -> dict[str, Any]:
    """
    Construit un scatter 2D avec mise en évidence des anomalies.

    Returns:
        dict Plotly JSON-serializable.
    """
    # ─── Validation de la config ──────────────────────────────────────
    x_col = config.get("x_col")
    y_col = config.get("y_col")
    if not x_col or not y_col:
        raise ValueError(
            "scatter_plot: x_col et y_col sont requis dans la config"
        )
    if x_col not in df.columns or y_col not in df.columns:
        raise ValueError(
            f"scatter_plot: colonnes manquantes dans le DataFrame "
            f"(x_col={x_col}, y_col={y_col})"
        )

    theme = get_theme(overrides=config.get("theme_overrides"))
    fig = go.Figure()

    # ─── Trace 1 : tous les points (normaux + anomalies) ──────────────
    # On les met en arrière-plan, en couleur unie de la palette.
    hover_text = None
    hover_col = config.get("hover_col")
    if hover_col and hover_col in df.columns:
        hover_text = [
            _format_hover(d, x_col, y_col, hover_col)
            for d in df.to_dict(orient="records")
        ]

    fig.add_trace(
        go.Scatter(
            x=df[x_col].tolist(),
            y=df[y_col].tolist(),
            mode="markers",
            name=config.get("normal_label", "Données"),
            marker=dict(
                size=6,
                color=get_series_color(0, theme),
                opacity=0.6,
            ),
            text=hover_text,
            hoverinfo="text" if hover_text else "x+y",
        )
    )

    # ─── Trace 2 : anomalies en surcouche (couleur d'alerte) ──────────
    anomaly_markers = config.get("anomaly_markers") or []
    if anomaly_markers:
        anomaly_x = [m.get("x") for m in anomaly_markers]
        anomaly_y = [m.get("y") for m in anomaly_markers]
        anomaly_text = [
            _format_anomaly_hover(m) for m in anomaly_markers
        ]
        anomaly_color = (
            theme.get("colors", {}).get("anomaly")
            or "#E74C3C"  # fallback sécurité
        )

        n = len(anomaly_markers)
        fig.add_trace(
            go.Scatter(
                x=anomaly_x,
                y=anomaly_y,
                mode="markers",
                name=f"{config.get('anomaly_label', 'Anomalies')} ({n})",
                marker=dict(
                    size=12,
                    color=anomaly_color,
                    line=dict(width=2, color="white"),
                    symbol="circle",
                ),
                text=anomaly_text,
                hoverinfo="text",
            )
        )

    # ─── Layout ───────────────────────────────────────────────────────
    layout: dict[str, Any] = {
        "title": config.get("title", f"{y_col} vs {x_col}"),
        "xaxis": {"title": config.get("x_label", x_col)},
        "yaxis": {"title": config.get("y_label", y_col)},
        "showlegend": True,
        "hovermode": "closest",
    }
    layout = apply_theme_to_layout(layout, theme)

    overrides = config.get("layout_overrides")
    if overrides:
        layout.update(overrides)

    fig.update_layout(**layout)
    return fig.to_dict()


# ─── Helpers de formatage hover ───────────────────────────────────────────


def _format_hover(
    record: dict[str, Any],
    x_col: str,
    y_col: str,
    hover_col: str,
) -> str:
    """Texte de hover pour un point normal."""
    parts = []
    if hover_col in record:
        parts.append(f"<b>{record[hover_col]}</b>")
    parts.append(f"{x_col}: {record.get(x_col)}")
    parts.append(f"{y_col}: {record.get(y_col)}")
    return "<br>".join(parts)


def _format_anomaly_hover(marker: dict[str, Any]) -> str:
    """Texte de hover pour une anomalie."""
    parts = ["<b>⚠ Anomalie</b>"]
    if "label" in marker:
        parts.append(str(marker["label"]))
    if "x" in marker:
        parts.append(f"x: {marker['x']}")
    if "y" in marker:
        parts.append(f"y: {marker['y']}")
    if "score" in marker:
        parts.append(f"score: {marker['score']:+.3f}")
    return "<br>".join(parts)
