"""
app/agents/analysis/stats
Fonctions statistiques pures pour l'Analysis Agent.

Ce sous-package ne contient aucun registry — ce sont des fonctions pures
appelées directement par les tasks. L'__init__ expose juste l'API publique
pour des imports plus courts depuis l'extérieur.
"""

from app.agents.analysis.stats.descriptive import (
    DEFAULT_MIN_POINTS_FOR_TREND,
    DEFAULT_QUANTILES,
    DEFAULT_TREND_FLAT_THRESHOLD,
    SHAPE_EMPTY,
    SHAPE_GROUPBY,
    SHAPE_NUMERIC_ONLY,
    SHAPE_TIMESERIES,
    SHAPE_UNKNOWN,
    detect_dataframe_shape,
    summarize_groupby,
    summarize_numeric,
    summarize_timeseries,
)

__all__ = [
    "DEFAULT_MIN_POINTS_FOR_TREND",
    "DEFAULT_QUANTILES",
    "DEFAULT_TREND_FLAT_THRESHOLD",
    "SHAPE_EMPTY",
    "SHAPE_GROUPBY",
    "SHAPE_NUMERIC_ONLY",
    "SHAPE_TIMESERIES",
    "SHAPE_UNKNOWN",
    "detect_dataframe_shape",
    "summarize_groupby",
    "summarize_numeric",
    "summarize_timeseries",
]
