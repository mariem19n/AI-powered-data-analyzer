"""
app/agents/analysis/stats/descriptive.py
Statistiques descriptives pures.

Cette couche ne sait rien :
- pas de notion de task ou de TaskResult
- pas de Plotly, pas de LLM, pas de KG
- pas de logique métier (crypto, macro, sentiment ne sont jamais mentionnés)
- aucun nom de colonne n'est hardcodé : tout est en paramètre ou détecté
  dynamiquement par dtype

Toutes les fonctions retournent des dicts JSON-friendly :
- np.float64 / np.int64 → float / int Python
- np.nan → None
- pd.Timestamp → ISO 8601 string

Quatre groupes de fonctions :
1. Helpers JSON-safe : _to_jsonable, _safe_number
2. Détection de shape : detect_dataframe_shape
3. Stats sur Series : summarize_numeric
4. Stats sur DataFrame : summarize_timeseries, summarize_groupby
"""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─── Constantes paramétrables──


# Quantiles par défaut. Toujours dépassables par l'appelant.
DEFAULT_QUANTILES: tuple[float, ...] = (0.25, 0.50, 0.75)

# Nombre minimum de points pour calculer une tendance (slope) sur une série
# temporelle. En dessous, le slope n'a pas de sens statistique.
DEFAULT_MIN_POINTS_FOR_TREND: int = 5

# Seuil par défaut pour classer une tendance via le slope normalisé
# (slope / mean). En dessous de ce seuil en valeur absolue, la tendance est
# "flat". Au-dessus, "up" si positif, "down" si négatif.
# Normalisé = indépendant de l'échelle (BTC à 80k$ vs FEDFUNDS à 4%).
DEFAULT_TREND_FLAT_THRESHOLD: float = 0.001  # 0.1% de variation par unité d'index


# Tags possibles pour detect_dataframe_shape. Pas une enum stricte volontairement
# (resterait extensible facilement) mais documenté ici comme contrat.
SHAPE_EMPTY = "empty"
SHAPE_TIMESERIES = "timeseries"
SHAPE_GROUPBY = "groupby"
SHAPE_NUMERIC_ONLY = "numeric_only"
SHAPE_UNKNOWN = "unknown"


# ─── Helpers JSON-safe ─────────────────────────────────────────────────────


def _safe_number(value: Any) -> float | int | None:
    """
    Convertit une valeur numérique éventuellement numpy/NaN/inf en
    float/int Python ou None. Utilisé partout pour garantir la
    sérialisation JSON.
    """
    if value is None:
        return None
    # Bool est sous-classe d'int en Python : on l'attrape explicitement avant.
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    # Pour pd.NA et autres marqueurs nuls.
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return None


def _to_iso(value: Any) -> str | None:
    """pd.Timestamp / datetime / np.datetime64 → ISO string. None sinon."""
    if value is None:
        return None
    try:
        ts = pd.Timestamp(value)
        if pd.isna(ts):
            return None
        return ts.isoformat()
    except (ValueError, TypeError):
        return None


# ─── Détection de la forme du DataFrame ────────────────────────────────────


def _find_datetime_columns(df: pd.DataFrame) -> list[str]:
    """Retourne les colonnes dont le dtype pandas est datetime."""
    return [c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])]


def _find_numeric_columns(df: pd.DataFrame) -> list[str]:
    """
    Retourne les colonnes numériques (int/float), excluant explicitement
    les bool et les datetime (qui sont aussi des sous-types numériques en numpy).
    """
    out: list[str] = []
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_bool_dtype(s):
            continue
        if pd.api.types.is_datetime64_any_dtype(s):
            continue
        if pd.api.types.is_numeric_dtype(s):
            out.append(c)
    return out


def _find_categorical_columns(df: pd.DataFrame) -> list[str]:
    """
    Retourne les colonnes catégorielles : object, category, bool, ou string.
    """
    out: list[str] = []
    for c in df.columns:
        s = df[c]
        if (
            pd.api.types.is_object_dtype(s)
            or pd.api.types.is_categorical_dtype(s)
            or pd.api.types.is_bool_dtype(s)
            or pd.api.types.is_string_dtype(s)
        ):
            out.append(c)
    return out


def detect_dataframe_shape(df: pd.DataFrame) -> dict[str, Any]:
    """
    Inspecte le DataFrame et retourne un tag décrivant sa forme, plus les
    colonnes détectées par catégorie.

    Aucun nom de colonne n'est hardcodé. La détection se fait uniquement
    sur les dtypes pandas.

    Tags possibles :
    - "empty"        : DataFrame vide
    - "timeseries"   : ≥1 col datetime + ≥1 col numérique
    - "groupby"      : ≥1 col catégorielle + ≥1 col numérique (et pas de date,
                       ou date ignorée car shape "timeseries" prend la priorité)
    - "numeric_only" : que des colonnes numériques (distribution pure)
    - "unknown"      : aucun pattern reconnu

    Retour :
    {
        "shape": "timeseries",
        "n_rows": 365,
        "n_cols": 3,
        "datetime_cols": ["date"],
        "numeric_cols": ["close_usd", "volume"],
        "categorical_cols": [],
    }
    """
    if df is None or df.empty:
        return {
            "shape": SHAPE_EMPTY,
            "n_rows": 0,
            "n_cols": 0 if df is None else int(len(df.columns)),
            "datetime_cols": [],
            "numeric_cols": [],
            "categorical_cols": [],
        }

    datetime_cols = _find_datetime_columns(df)
    numeric_cols = _find_numeric_columns(df)
    categorical_cols = _find_categorical_columns(df)

    if datetime_cols and numeric_cols:
        shape = SHAPE_TIMESERIES
    elif categorical_cols and numeric_cols:
        shape = SHAPE_GROUPBY
    elif numeric_cols and not categorical_cols and not datetime_cols:
        shape = SHAPE_NUMERIC_ONLY
    else:
        shape = SHAPE_UNKNOWN

    return {
        "shape": shape,
        "n_rows": int(len(df)),
        "n_cols": int(len(df.columns)),
        "datetime_cols": datetime_cols,
        "numeric_cols": numeric_cols,
        "categorical_cols": categorical_cols,
    }


# ─── Stats sur une Series numérique ────────────────────────────────────────


def summarize_numeric(
    series: pd.Series,
    quantiles: tuple[float, ...] = DEFAULT_QUANTILES,
) -> dict[str, Any]:
    """
    Statistiques descriptives d'une Series numérique.

    Gère NaN proprement : les NaN sont exclus du calcul ET comptés dans
    `n_missing`. Si après dropna la série est vide, retourne un dict avec
    n=0 et toutes les stats à None.

    Args:
        series: Series Pandas, doit être numérique (vérifié).
        quantiles: tuple de quantiles à calculer. Les clés du dict de sortie
            seront construites dynamiquement : 0.25 → "q25", 0.5 → "q50".

    Returns:
        {
            "n": int,
            "n_missing": int,
            "mean": float | None,
            "std": float | None,
            "min": float | None,
            "max": float | None,
            "quantiles": {"q25": ..., "q50": ..., "q75": ...},
            "skew": float | None,
            "kurtosis": float | None,
        }
    """
    if not pd.api.types.is_numeric_dtype(series):
        raise TypeError(
            f"summarize_numeric attend une Series numérique, "
            f"reçu dtype={series.dtype}"
        )

    n_total = int(len(series))
    clean = series.dropna()
    n = int(len(clean))
    n_missing = n_total - n

    if n == 0:
        # Quantiles à None mais clés présentes pour stabilité du schéma.
        empty_quantiles = {f"q{int(q * 100)}": None for q in quantiles}
        return {
            "n": 0,
            "n_missing": n_missing,
            "mean": None,
            "std": None,
            "min": None,
            "max": None,
            "quantiles": empty_quantiles,
            "skew": None,
            "kurtosis": None,
        }

    # std avec ddof=1 (échantillon) — convention scipy/pandas standard.
    # Skew et kurtosis sont None si n < 3 ou si la variance est nulle.
    can_compute_higher_moments = n >= 3 and clean.std(ddof=1) > 0

    quantile_values = clean.quantile(list(quantiles))
    quantile_dict = {
        f"q{int(q * 100)}": _safe_number(quantile_values.loc[q])
        for q in quantiles
    }

    return {
        "n": n,
        "n_missing": n_missing,
        "mean": _safe_number(clean.mean()),
        "std": _safe_number(clean.std(ddof=1)) if n >= 2 else None,
        "min": _safe_number(clean.min()),
        "max": _safe_number(clean.max()),
        "quantiles": quantile_dict,
        "skew": _safe_number(clean.skew()) if can_compute_higher_moments else None,
        "kurtosis": (
            _safe_number(clean.kurtosis()) if can_compute_higher_moments else None
        ),
    }


# ─── Stats sur une série temporelle ────────────────────────────────────────


def _classify_trend(
    slope_normalized: float | None,
    flat_threshold: float,
) -> str | None:
    """
    Classe un slope normalisé en 'up' / 'down' / 'flat'.

    `slope_normalized` = slope / mean — donc indépendant de l'échelle des
    valeurs. Le seuil `flat_threshold` s'applique en valeur absolue.

    Retourne None si le slope normalisé n'est pas calculable.
    """
    if slope_normalized is None:
        return None
    if abs(slope_normalized) < flat_threshold:
        return "flat"
    return "up" if slope_normalized > 0 else "down"


def summarize_timeseries(
    df: pd.DataFrame,
    date_col: str,
    value_col: str,
    quantiles: tuple[float, ...] = DEFAULT_QUANTILES,
    min_points_for_trend: int = DEFAULT_MIN_POINTS_FOR_TREND,
    trend_flat_threshold: float = DEFAULT_TREND_FLAT_THRESHOLD,
) -> dict[str, Any]:
    """
    Statistiques sur une série temporelle (date + valeur numérique).

    Inclut toutes les stats de summarize_numeric + first, last,
    pct_change_total, slope linéaire, trend_direction, date_min, date_max.

    Args:
        df: DataFrame contenant au moins `date_col` et `value_col`.
        date_col: nom de la colonne datetime.
        value_col: nom de la colonne numérique.
        quantiles: voir summarize_numeric.
        min_points_for_trend: en dessous, slope et trend_direction sont None.
        trend_flat_threshold: seuil sur |slope/mean| pour classer 'flat'.

    Returns:
        Dict JSON-friendly. Toutes les clés sont toujours présentes (None si
        non calculable) pour stabilité du schéma côté KG et LLM.

    Raises:
        KeyError: si date_col ou value_col absents du DataFrame.
        TypeError: si les dtypes ne correspondent pas.
    """
    if date_col not in df.columns:
        raise KeyError(f"date_col '{date_col}' absent du DataFrame")
    if value_col not in df.columns:
        raise KeyError(f"value_col '{value_col}' absent du DataFrame")
    if not pd.api.types.is_datetime64_any_dtype(df[date_col]):
        raise TypeError(
            f"date_col '{date_col}' n'est pas datetime "
            f"(dtype={df[date_col].dtype})"
        )
    if not pd.api.types.is_numeric_dtype(df[value_col]):
        raise TypeError(
            f"value_col '{value_col}' n'est pas numérique "
            f"(dtype={df[value_col].dtype})"
        )

    # Trier par date pour que first/last/slope aient un sens.
    sorted_df = df[[date_col, value_col]].sort_values(date_col).reset_index(drop=True)

    base_stats = summarize_numeric(sorted_df[value_col], quantiles=quantiles)

    # Min/max de date toujours calculables si n_total > 0.
    n_total = len(sorted_df)
    date_min = _to_iso(sorted_df[date_col].min()) if n_total else None
    date_max = _to_iso(sorted_df[date_col].max()) if n_total else None

    # first / last sur valeurs non nulles : on ignore les NaN aux extrémités
    # pour éviter "first=None alors qu'on a 364 points valides".
    clean_pairs = sorted_df.dropna(subset=[value_col]).reset_index(drop=True)
    n_clean = len(clean_pairs)

    first_value = (
        _safe_number(clean_pairs[value_col].iloc[0]) if n_clean > 0 else None
    )
    last_value = (
        _safe_number(clean_pairs[value_col].iloc[-1]) if n_clean > 0 else None
    )
    first_date = _to_iso(clean_pairs[date_col].iloc[0]) if n_clean > 0 else None
    last_date = _to_iso(clean_pairs[date_col].iloc[-1]) if n_clean > 0 else None

    # pct_change_total : (last - first) / |first|, robuste au signe.
    pct_change_total: float | None
    if (
        first_value is not None
        and last_value is not None
        and first_value != 0
    ):
        pct_change_total = (last_value - first_value) / abs(first_value)
    else:
        pct_change_total = None

    # Trend slope : régression linéaire OLS sur (index, valeur).
    # On utilise polyfit(deg=1) — suffisant pour un slope, pas besoin de scipy.
    # On indexe sur la position (0, 1, 2, ...) plutôt que sur le timestamp.
    # Conséquence : `slope` est en "unités de valeur par observation".
    # Pour normaliser indépendamment de l'échelle, on calcule slope / mean.
    slope: float | None = None
    slope_normalized: float | None = None
    if n_clean >= min_points_for_trend:
        x = np.arange(n_clean, dtype=float)
        y = clean_pairs[value_col].to_numpy(dtype=float)
        try:
            coeffs = np.polyfit(x, y, deg=1)
            slope = _safe_number(coeffs[0])
            mean_val = base_stats.get("mean")
            if (
                slope is not None
                and isinstance(mean_val, (int, float))
                and mean_val != 0
            ):
                slope_normalized = slope / abs(mean_val)
        except (np.linalg.LinAlgError, ValueError) as e:
            logger.debug("polyfit failed: %s", e)

    trend_direction = _classify_trend(slope_normalized, trend_flat_threshold)

    return {
        **base_stats,
        "first": first_value,
        "last": last_value,
        "first_date": first_date,
        "last_date": last_date,
        "date_min": date_min,
        "date_max": date_max,
        "pct_change_total": _safe_number(pct_change_total),
        "trend_slope": slope,
        "trend_slope_normalized": _safe_number(slope_normalized),
        "trend_direction": trend_direction,
        "trend_flat_threshold_used": trend_flat_threshold,
    }


# ─── Stats sur un groupby ──────────────────────────────────────────────────


def summarize_groupby(
    df: pd.DataFrame,
    group_col: str,
    value_col: str,
    quantiles: tuple[float, ...] = DEFAULT_QUANTILES,
    max_groups: int | None = None,
) -> dict[str, Any]:
    """
    Statistiques par groupe : count, sum, mean, median, std, min, max
    pour chaque valeur unique de group_col.

    Args:
        df: DataFrame contenant group_col et value_col.
        group_col: colonne catégorielle de regroupement.
        value_col: colonne numérique à agréger.
        quantiles: quantiles calculés au niveau global (pas par groupe).
        max_groups: si fourni, limite le nombre de groupes retournés (pris
            par count décroissant). Utile pour éviter d'envoyer 10 000 groupes
            au LLM. None = pas de limite.

    Returns:
        {
            "n_groups": int,
            "n_groups_returned": int,
            "groups": [
                {"group": "BTC", "count": 365, "sum": ..., "mean": ..., ...},
                ...
            ],
            "global_stats": {... summarize_numeric sur value_col entier ...}
        }
    """
    if group_col not in df.columns:
        raise KeyError(f"group_col '{group_col}' absent du DataFrame")
    if value_col not in df.columns:
        raise KeyError(f"value_col '{value_col}' absent du DataFrame")
    if not pd.api.types.is_numeric_dtype(df[value_col]):
        raise TypeError(
            f"value_col '{value_col}' n'est pas numérique "
            f"(dtype={df[value_col].dtype})"
        )

    if df.empty:
        return {
            "n_groups": 0,
            "n_groups_returned": 0,
            "groups": [],
            "global_stats": summarize_numeric(df[value_col], quantiles=quantiles),
        }

    grouped = df.groupby(group_col, dropna=False)[value_col]

    # On calcule tout d'un coup pour rester performant sur de gros DataFrames.
    agg = grouped.agg(["count", "sum", "mean", "median", "std", "min", "max"])
    n_groups = int(len(agg))

    # Tri par count desc puis limitation éventuelle.
    agg_sorted = agg.sort_values("count", ascending=False)
    if max_groups is not None and max_groups > 0:
        agg_sorted = agg_sorted.head(max_groups)

    groups_out: list[dict[str, Any]] = []
    for group_value, row in agg_sorted.iterrows():
        groups_out.append(
            {
                "group": (
                    None if pd.isna(group_value) else str(group_value)
                ),  # str pour JSON safety (objets, ints, etc.)
                "count": _safe_number(row["count"]),
                "sum": _safe_number(row["sum"]),
                "mean": _safe_number(row["mean"]),
                "median": _safe_number(row["median"]),
                "std": _safe_number(row["std"]),
                "min": _safe_number(row["min"]),
                "max": _safe_number(row["max"]),
            }
        )

    return {
        "n_groups": n_groups,
        "n_groups_returned": len(groups_out),
        "groups": groups_out,
        "global_stats": summarize_numeric(df[value_col], quantiles=quantiles),
    }
