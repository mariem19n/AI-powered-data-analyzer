"""
app/agents/analysis/viz/line_chart.py
Visualisation : graphique en ligne pour séries temporelles.

S'enregistre sous le nom "line" et comme viz par défaut pour SHAPE_TIMESERIES.

Caractéristiques :
- Mono-série ou multi-séries (une trace par colonne y).
- Marqueurs optionnels (utiles pour visualiser des anomalies par-dessus la
  ligne quand la task anomaly réutilisera ce chart plus tard).
- Lignes de référence horizontales optionnelles (ex : moyenne, seuils).
- Aucun nom de colonne, couleur ou taille n'est hardcodé : tout vient de la
  config (passée par la task) ou du theme (centralisé).

Le retour est un dict Plotly JSON-serializable (équivalent fig.to_dict()).
"""

from __future__ import annotations

import json
import logging
from typing import Any, TypedDict

import pandas as pd
import plotly.graph_objects as go
from plotly.utils import PlotlyJSONEncoder

from app.agents.analysis.stats.descriptive import SHAPE_TIMESERIES
from app.agents.analysis.viz.templates import (
    register_default_for_shape,
    register_viz,
)
from app.agents.analysis.viz.theme import (
    apply_theme_to_layout,
    get_series_color,
    get_theme,
)

logger = logging.getLogger(__name__)


# ─── Contrat de configuration ─────────────────────────────────────────────


class ReferenceLine(TypedDict, total=False):
    """Ligne de référence horizontale optionnelle (moyenne, seuil, ...)."""

    y: float                # valeur sur l'axe Y
    label: str              # libellé affiché dans la légende
    color: str              # couleur explicite (sinon thème.colors.reference)
    dash: str               # style du trait : 'solid' | 'dot' | 'dash' | 'dashdot'


class MarkerOverlay(TypedDict, total=False):
    """
    Marqueurs ponctuels superposés sur les lignes (pour anomalies / events).

    `series` désigne quelle colonne y porte les marqueurs ; doit être présent
    dans `y_cols`. `x_values` et `y_values` sont les coordonnées des points.
    """

    series: str
    x_values: list[Any]
    y_values: list[float]
    label: str
    color: str
    size: int


class LineChartConfig(TypedDict, total=False):
    """
    Configuration d'un line chart.

    Champs requis :
    - x_col : nom de la colonne de l'axe X (typiquement datetime)
    - y_cols : list des colonnes numériques à tracer (1 = mono-série, n = multi)

    Champs optionnels :
    - title, x_label, y_label : texte du titre et libellés des axes.
                                Si absents : x_col / y_cols[0] (ou rien) sont utilisés.
    - mode : style des traces — 'lines' | 'lines+markers' | 'markers'
             (par défaut 'lines')
    - series_labels : dict {y_col: label_affiché} pour renommer les légendes
    - reference_lines : list[ReferenceLine] superposées
    - marker_overlays : list[MarkerOverlay] (pour anomalies, events, ...)
    - theme_overrides : dict d'overrides à passer à get_theme()
    - layout_overrides : dict de layout Plotly à appliquer EN DERNIER
                        (gagne sur le thème) — pour les besoins exotiques
    """

    x_col: str
    y_cols: list[str]
    title: str
    x_label: str
    y_label: str
    mode: str
    series_labels: dict[str, str]
    reference_lines: list[ReferenceLine]
    marker_overlays: list[MarkerOverlay]
    theme_overrides: dict[str, Any]
    layout_overrides: dict[str, Any]


# ─── Implémentation ───────────────────────────────────────────────────────


_VALID_MODES = ("lines", "lines+markers", "markers")
_VALID_DASHES = ("solid", "dot", "dash", "dashdot", "longdash", "longdashdot")


def _plotly_json_safe(fig: go.Figure) -> dict[str, Any]:
    """
    Convertit une figure Plotly en dict JSON-safe.

    fig.to_dict() garde parfois des numpy.ndarray pour x/y quand la figure
    vient de Pandas. PlotlyJSONEncoder les convertit en listes Python.
    """
    return json.loads(json.dumps(fig.to_dict(), cls=PlotlyJSONEncoder))


def _validate_config(df: pd.DataFrame, config: dict[str, Any]) -> None:
    """
    Vérifie que la config est cohérente avec le DataFrame.
    Lève ValueError avec un message explicite si un problème est détecté.
    """
    x_col = config.get("x_col")
    y_cols = config.get("y_cols")

    if not isinstance(x_col, str) or not x_col:
        raise ValueError("line_chart: config['x_col'] est requis (str non vide)")
    if not isinstance(y_cols, list) or not y_cols:
        raise ValueError(
            "line_chart: config['y_cols'] est requis (list non vide)"
        )

    if x_col not in df.columns:
        raise ValueError(
            f"line_chart: x_col '{x_col}' absent du DataFrame "
            f"(colonnes disponibles : {list(df.columns)})"
        )
    missing_y = [c for c in y_cols if c not in df.columns]
    if missing_y:
        raise ValueError(
            f"line_chart: y_cols absent(s) du DataFrame : {missing_y} "
            f"(colonnes disponibles : {list(df.columns)})"
        )

    mode = config.get("mode", "lines")
    if mode not in _VALID_MODES:
        raise ValueError(
            f"line_chart: mode='{mode}' invalide. "
            f"Valeurs acceptées : {_VALID_MODES}"
        )


def _series_label(y_col: str, config: dict[str, Any]) -> str:
    """Récupère le label affiché pour une colonne y, défaut = nom de la colonne."""
    labels = config.get("series_labels") or {}
    return labels.get(y_col, y_col)


def _build_main_traces(
    df: pd.DataFrame,
    config: dict[str, Any],
    theme: dict[str, Any],
) -> list[go.Scatter]:
    """Construit une trace Scatter par colonne y demandée."""
    x_col: str = config["x_col"]
    y_cols: list[str] = config["y_cols"]
    mode: str = config.get("mode", "lines")

    traces: list[go.Scatter] = []
    for i, y_col in enumerate(y_cols):
        color = get_series_color(i, theme)
        traces.append(
            go.Scatter(
                x=df[x_col].tolist(),
                y=df[y_col].tolist(),
                mode=mode,
                name=_series_label(y_col, config),
                line={"color": color, "width": 2},
                marker={"color": color, "size": 6} if "markers" in mode else None,
                connectgaps=False,  # un trou (NaN) reste un trou, pas une interpolation
            )
        )
    return traces


def _build_reference_lines(
    df: pd.DataFrame,
    config: dict[str, Any],
    theme: dict[str, Any],
) -> list[go.Scatter]:
    """
    Construit des lignes horizontales optionnelles via deux points (xmin, xmax).
    Préféré à `shapes` dans le layout pour qu'elles apparaissent dans la légende
    et que le hover fonctionne.
    """
    x_col: str = config["x_col"]
    refs = config.get("reference_lines") or []
    if not refs:
        return []

    if df.empty:
        return []

    # Bornes X pour tracer les lignes horizontales.
    x_min = df[x_col].min()
    x_max = df[x_col].max()

    out: list[go.Scatter] = []
    for ref in refs:
        if "y" not in ref:
            logger.warning("line_chart: reference_line sans 'y' ignorée: %r", ref)
            continue
        y_value = ref["y"]
        color = ref.get("color", theme["colors"]["reference"])
        dash = ref.get("dash", "dash")
        if dash not in _VALID_DASHES:
            logger.warning(
                "line_chart: dash='%s' invalide, fallback 'dash'", dash
            )
            dash = "dash"
        label = ref.get("label", f"y = {y_value}")

        out.append(
            go.Scatter(
                x=[x_min, x_max],
                y=[y_value, y_value],
                mode="lines",
                name=label,
                line={"color": color, "width": 1.5, "dash": dash},
                hoverinfo="name+y",
                showlegend=True,
            )
        )
    return out


def _build_marker_overlays(
    config: dict[str, Any],
    theme: dict[str, Any],
) -> list[go.Scatter]:
    """
    Construit des traces de marqueurs ponctuels (utilisé par anomaly task pour
    superposer des points "anomalie" sur les lignes existantes).
    """
    overlays = config.get("marker_overlays") or []
    if not overlays:
        return []

    y_cols_set = set(config.get("y_cols") or [])
    out: list[go.Scatter] = []

    for ov in overlays:
        x_values = ov.get("x_values") or []
        y_values = ov.get("y_values") or []
        if len(x_values) != len(y_values):
            logger.warning(
                "line_chart: marker_overlay x/y de tailles différentes "
                "(%d vs %d), ignoré",
                len(x_values),
                len(y_values),
            )
            continue
        if not x_values:
            # overlay vide -> on n'ajoute pas de trace bruyante
            continue

        series = ov.get("series")
        if series is not None and series not in y_cols_set:
            logger.warning(
                "line_chart: marker_overlay.series='%s' n'est pas dans y_cols, "
                "marqueur affiché quand même",
                series,
            )

        color = ov.get("color", theme["colors"]["anomaly"])
        size = ov.get("size", 10)
        label = ov.get("label", "events")

        out.append(
            go.Scatter(
                x=x_values,
                y=y_values,
                mode="markers",
                name=label,
                marker={
                    "color": color,
                    "size": size,
                    "symbol": "circle",
                    "line": {"width": 1.5, "color": "#FFFFFF"},
                },
                hoverinfo="x+y+name",
                showlegend=True,
            )
        )
    return out


def _build_layout(config: dict[str, Any], theme: dict[str, Any]) -> dict[str, Any]:
    """Construit le layout final via apply_theme_to_layout + overrides utilisateur."""
    title = config.get("title")
    x_label = config.get("x_label", config.get("x_col", ""))
    y_cols = config.get("y_cols", [])
    # Pour un mono-série, on prend le nom de la série comme y_label par défaut.
    # Pour un multi-série, on laisse vide (la légende fait le travail).
    default_y_label = y_cols[0] if len(y_cols) == 1 else ""
    y_label = config.get("y_label", default_y_label)

    user_layout: dict[str, Any] = {
        "xaxis": {"title": {"text": x_label}},
        "yaxis": {"title": {"text": y_label}},
    }
    if title:
        user_layout["title"] = {"text": title}

    # apply_theme_to_layout fait le merge avec priorité à user_layout pour les
    # conflits, sauf qu'on a des overrides explicites en plus.
    layout = apply_theme_to_layout(user_layout, theme=theme)

    # layout_overrides : appelant explicite, gagne en dernier.
    extra = config.get("layout_overrides") or {}
    if extra:
        # Reuse : apply_theme_to_layout merge récursivement.
        layout = apply_theme_to_layout(extra, theme=theme) | layout
        # Le simple pipe (|) garde layout sur extra ; on veut l'inverse :
        # extra doit gagner. Donc on refait dans l'autre sens en deep-merge.
        # On utilise apply_theme_to_layout(layout=extra) puis merge à la main :
        for k, v in extra.items():
            if (
                isinstance(v, dict)
                and k in layout
                and isinstance(layout[k], dict)
            ):
                layout[k] = {**layout[k], **v}
            else:
                layout[k] = v

    return layout


@register_default_for_shape(SHAPE_TIMESERIES)
@register_viz("line")
def line_chart(df: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any]:
    """
    Construit un line chart Plotly à partir d'un DataFrame et d'une config.

    Args:
        df: DataFrame contenant au minimum les colonnes x_col et y_cols.
        config: voir LineChartConfig pour la liste des champs supportés.

    Returns:
        Dict Plotly JSON-serializable (sortie de fig.to_dict()).

    Raises:
        ValueError: si la config est incohérente (col absente, mode invalide).
    """
    _validate_config(df, config)

    theme = get_theme(config.get("theme_overrides"))

    fig = go.Figure()
    for trace in _build_main_traces(df, config, theme):
        fig.add_trace(trace)
    for trace in _build_reference_lines(df, config, theme):
        fig.add_trace(trace)
    for trace in _build_marker_overlays(config, theme):
        fig.add_trace(trace)

    layout = _build_layout(config, theme)
    fig.update_layout(**layout)

    return _plotly_json_safe(fig)
