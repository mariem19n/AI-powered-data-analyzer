"""
app/agents/analysis/viz/aggregation_chart.py
Visualisation dédiée aux réponses d'agrégation.

Elle s'enregistre dans le registre global sous "aggregation_summary".
Selon la forme des données :
- plusieurs points datés : courbe temporelle + ligne de référence de l'agrégat ;
- une seule ligne agrégée : barre synthétique de la valeur calculée.
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.utils import PlotlyJSONEncoder

from app.agents.analysis.viz.templates import register_viz
from app.agents.analysis.viz.theme import apply_theme_to_layout, get_theme


def _plotly_json_safe(fig: go.Figure) -> dict[str, Any]:
    return json.loads(json.dumps(fig.to_dict(), cls=PlotlyJSONEncoder))


def _format_hover_value(value: Any, unit: str | None) -> str:
    if value is None:
        return ""
    return f"{value} {unit}" if unit else str(value)


@register_viz("aggregation_summary")
def aggregation_chart(df: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any]:
    """
    Construit une visualisation Plotly pour une agrégation.

    Args:
        df: DataFrame utilisé par la task aggregation.
        config: configuration construite par la task. Champs principaux :
            value_col, date_col, aggregate_value, aggregate_label, metric, unit.

    Returns:
        Dict Plotly JSON-serializable.
    """
    value_col = config.get("value_col")
    if not isinstance(value_col, str) or value_col not in df.columns:
        raise ValueError("aggregation_chart: value_col absent ou invalide")

    metric = str(config.get("metric") or value_col)
    aggregate_label = str(config.get("aggregate_label") or "Valeur agrégée")
    aggregate_value = config.get("aggregate_value")
    unit = config.get("unit")
    date_col = config.get("date_col")
    title = str(config.get("title") or f"{aggregate_label} - {metric}")

    theme = get_theme(config.get("theme_overrides"))
    primary = theme["colors"]["primary"]
    reference = theme["colors"]["reference"]
    fig = go.Figure()

    has_timeseries = (
        isinstance(date_col, str)
        and date_col in df.columns
        and len(df) > 1
    )

    if has_timeseries:
        fig.add_trace(
            go.Scatter(
                x=df[date_col].tolist(),
                y=df[value_col].tolist(),
                mode="lines+markers",
                name=metric,
                line={"color": primary, "width": 2.5},
                marker={"color": primary, "size": 6},
                hovertemplate="%{x}<br>%{y}<extra>" + metric + "</extra>",
            )
        )

        if aggregate_value is not None:
            x_min = df[date_col].min()
            x_max = df[date_col].max()
            fig.add_trace(
                go.Scatter(
                    x=[x_min, x_max],
                    y=[aggregate_value, aggregate_value],
                    mode="lines",
                    name=aggregate_label,
                    line={"color": reference, "width": 2, "dash": "dash"},
                    hovertemplate=(
                        f"{aggregate_label}: "
                        f"{_format_hover_value(config.get('formatted_value') or aggregate_value, unit)}"
                        "<extra></extra>"
                    ),
                )
            )
    else:
        fig.add_trace(
            go.Bar(
                x=[aggregate_label],
                y=[aggregate_value],
                name=metric,
                marker={"color": primary},
                text=[config.get("formatted_value") or aggregate_value],
                textposition="auto",
                hovertemplate="%{x}<br>%{text}<extra></extra>",
            )
        )

    layout = apply_theme_to_layout(
        {
            "title": {"text": title},
            "xaxis": {"title": {"text": config.get("x_label") or ""}},
            "yaxis": {"title": {"text": config.get("y_label") or metric}},
            "showlegend": has_timeseries,
        },
        theme=theme,
    )
    fig.update_layout(**layout)

    return _plotly_json_safe(fig)
