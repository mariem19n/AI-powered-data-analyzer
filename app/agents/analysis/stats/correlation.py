"""
app/agents/analysis/stats/correlation.py

Fonctions pures de calcul de corrélation entre N séries temporelles.

Conventions
-----------
1. Aucune I/O ici (pas de DB, pas de Redis, pas de fichier).
2. Aucun appel LLM ici.
3. Tous les outputs sont JSON-safe (float natif, str natif, list/dict).
4. Toute condition de fragilité (échantillon trop petit, série constante,
   trop de NaN) renvoie un warning non-bloquant, jamais une exception.
5. Aucun nom de colonne / domaine hardcodé. Les noms sont fournis par
   l'appelant via le DataFrame wide.

Contrat d'entrée
-----------------
La fonction `compute_correlations` reçoit un DataFrame "wide" :
  - index : date (DatetimeIndex) OU colonne nommée `date_col`
  - colonnes : N séries numériques (≥ 2)

C'est la responsabilité de la TASK (pas du stats module) de fabriquer ce
DataFrame wide depuis les N DataFrames "long" produits par les steps SQL.

Contrat de sortie
------------------
Un dict structuré, JSON-safe, contenant :
  - n_series, series_names, n_points (sample size)
  - levels : matrices pearson/spearman + paires fortes/faibles
  - returns : idem sur returns (pct_change si finance, sinon diff)
  - method_notes : pourquoi on fait les deux, comment lire l'écart
  - warnings : list[str]
"""

from __future__ import annotations

import logging
import math
from typing import Any, Literal

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─── Constantes paramétrables (pas de magic numbers inline) ───────────────

# Nombre minimum de points en commun (après dropna sur les paires) pour
# considérer une corrélation comme fiable. En-dessous : warning + on calcule
# quand même, mais la confidence sera dégradée côté insight.
DEFAULT_MIN_POINTS: int = 30

# Seuil d'écart Pearson-Spearman au-delà duquel on signale une relation
# probablement non-linéaire ou contaminée par outliers.
PEARSON_SPEARMAN_DIVERGENCE_THRESHOLD: float = 0.20

# Seuils d'interprétation des |corr|. Utilisés pour étiqueter les paires
# fortes/modérées/faibles. Conformes aux conventions usuelles en finance.
STRONG_THRESHOLD: float = 0.70
MODERATE_THRESHOLD: float = 0.40

# Nombre maximum de paires retournées dans top_pairs (top + bottom). Sert
# uniquement à borner la taille du payload LLM si le DataFrame a beaucoup
# de colonnes.
TOP_PAIRS_PER_DIRECTION: int = 5


# ─── API publique ──────────────────────────────────────────────────────────


def compute_correlations(
    df_wide: pd.DataFrame,
    *,
    date_col: str | None = None,
    series_cols: list[str] | None = None,
    min_points: int = DEFAULT_MIN_POINTS,
    pretreatment: Literal["returns", "levels", "both"] = "both",
) -> dict[str, Any]:
    """
    Calcule les matrices de corrélation Pearson et Spearman sur N séries.

    Args:
        df_wide: DataFrame wide. Soit l'index est déjà une date, soit
            une colonne `date_col` est présente.
        date_col: Nom de la colonne date si non indexée. Si None et que
            l'index est un DatetimeIndex, on utilise l'index.
        series_cols: Liste explicite des colonnes-séries à corréler.
            Si None, on prend toutes les colonnes numériques (sauf date_col).
        min_points: Seuil de fiabilité de l'échantillon.
        pretreatment: "returns" (recommandé en finance), "levels" (brut),
            ou "both" (les deux). Sortie structurée en conséquence.

    Returns:
        Dict JSON-safe — voir `_build_empty_payload` pour la structure.
    """
    warnings: list[str] = []
    payload = _build_empty_payload(pretreatment=pretreatment)

    # 1. Normalisation : extraction des séries + index temporel propre.
    wide, prep_warnings = _normalize_wide(
        df_wide, date_col=date_col, series_cols=series_cols
    )
    warnings.extend(prep_warnings)

    if wide is None or wide.shape[1] < 2:
        warnings.append(
            "Corrélation impossible : moins de 2 séries numériques disponibles "
            "après nettoyage."
        )
        payload["warnings"] = warnings
        return payload

    series_names = list(wide.columns)
    n_points_raw = int(len(wide))

    payload["n_series"] = len(series_names)
    payload["series_names"] = series_names
    payload["n_points_raw"] = n_points_raw

    if n_points_raw < min_points:
        warnings.append(
            f"Échantillon faible ({n_points_raw} points < {min_points} requis). "
            "Les corrélations sont calculées mais leur fiabilité est dégradée."
        )

    # 2. Calcul sur levels (si demandé).
    if pretreatment in ("levels", "both"):
        levels_block, levels_warn = _compute_block(
            wide,
            block_name="levels",
            min_points=min_points,
        )
        payload["levels"] = levels_block
        warnings.extend(levels_warn)

    # 3. Calcul sur returns (si demandé).
    if pretreatment in ("returns", "both"):
        returns_df, ret_warn = _to_returns(wide)
        warnings.extend(ret_warn)

        if returns_df is not None and returns_df.shape[1] >= 2:
            returns_block, ret_block_warn = _compute_block(
                returns_df,
                block_name="returns",
                min_points=min_points,
            )
            payload["returns"] = returns_block
            warnings.extend(ret_block_warn)
        else:
            warnings.append(
                "Returns non calculables (DataFrame insuffisant après pct_change)."
            )

    # 4. Notes méthodologiques pour le LLM.
    payload["method_notes"] = _build_method_notes(pretreatment)
    payload["warnings"] = warnings

    return payload


def summarize_correlation(
    correlation_result: dict[str, Any],
) -> dict[str, Any]:
    """
    Réduit le payload complet en stats compactes pour le prompt LLM.

    Le payload complet contient les matrices entières. Le LLM n'a pas besoin
    de tout ça : il a besoin des paires significatives, des divergences
    Pearson/Spearman, et de la taille d'échantillon. Cette fonction extrait
    ces signaux et produit un dict minimal.

    Returns:
        Dict prêt à être passé en `stats` à InsightGenerator.generate().
    """
    summary: dict[str, Any] = {
        "n_series": correlation_result.get("n_series", 0),
        "series_names": correlation_result.get("series_names", []),
        "n_points_raw": correlation_result.get("n_points_raw", 0),
        "warnings": list(correlation_result.get("warnings", [])),
    }

    for block_name in ("levels", "returns"):
        block = correlation_result.get(block_name)
        if not block:
            continue
        summary[block_name] = {
            "n_points_used": block.get("n_points_used", 0),
            "top_positive_pairs": block.get("top_positive_pairs", []),
            "top_negative_pairs": block.get("top_negative_pairs", []),
            "strong_pairs_count": block.get("strong_pairs_count", 0),
            "divergent_pairs": block.get("divergent_pairs", []),
        }

    return summary


# ─── Helpers internes ──────────────────────────────────────────────────────


def _build_empty_payload(
    *, pretreatment: Literal["returns", "levels", "both"]
) -> dict[str, Any]:
    """Structure de base, toujours retournée (même en cas d'échec)."""
    payload: dict[str, Any] = {
        "n_series": 0,
        "series_names": [],
        "n_points_raw": 0,
        "pretreatment_requested": pretreatment,
        "method_notes": "",
        "warnings": [],
    }
    if pretreatment in ("levels", "both"):
        payload["levels"] = None
    if pretreatment in ("returns", "both"):
        payload["returns"] = None
    return payload


def _normalize_wide(
    df: pd.DataFrame,
    *,
    date_col: str | None,
    series_cols: list[str] | None,
) -> tuple[pd.DataFrame | None, list[str]]:
    """
    Garantit qu'on a un DataFrame indexé par date avec uniquement les séries
    numériques d'intérêt. Trie par date asc et déduplique l'index.
    """
    warnings: list[str] = []

    if df is None or df.empty:
        warnings.append("DataFrame d'entrée vide.")
        return None, warnings

    work = df.copy()

    # Établir l'index temporel.
    if date_col and date_col in work.columns:
        try:
            work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
        except Exception as e:  # noqa: BLE001
            warnings.append(f"Conversion date échouée sur '{date_col}' : {e}")
            return None, warnings
        work = work.dropna(subset=[date_col])
        work = work.set_index(date_col)
    elif isinstance(work.index, pd.DatetimeIndex):
        pass  # OK
    else:
        # Tentative best-effort : si l'index est convertible en date, on s'en sert.
        try:
            new_idx = pd.to_datetime(work.index, errors="coerce")
            if new_idx.notna().any():
                work.index = new_idx
                work = work[work.index.notna()]
            else:
                warnings.append(
                    "Aucun index temporel détecté (pas de DatetimeIndex et "
                    "pas de `date_col` valide). Corrélation calculable mais "
                    "non temporelle."
                )
        except Exception:  # noqa: BLE001
            warnings.append(
                "Index temporel introuvable. Corrélation calculée sur l'ordre "
                "des lignes brutes."
            )

    # Sélection des colonnes-séries.
    if series_cols:
        keep = [c for c in series_cols if c in work.columns]
        missing = [c for c in series_cols if c not in work.columns]
        if missing:
            warnings.append(
                f"Colonnes demandées absentes : {missing}. Ignorées."
            )
        work = work[keep]
    else:
        # Conserver uniquement les numériques.
        num_cols = [
            c
            for c in work.columns
            if pd.api.types.is_numeric_dtype(work[c])
        ]
        work = work[num_cols]

    if work.shape[1] < 2:
        warnings.append("Moins de 2 colonnes numériques.")
        return None, warnings

    # Tri + déduplication d'index (la dernière valeur l'emporte).
    if isinstance(work.index, pd.DatetimeIndex):
        work = work.sort_index()
        if work.index.duplicated().any():
            warnings.append(
                "Index dates dupliqués : conservation de la dernière "
                "observation par date."
            )
            work = work[~work.index.duplicated(keep="last")]

    return work, warnings


def _to_returns(wide: pd.DataFrame) -> tuple[pd.DataFrame | None, list[str]]:
    """
    Calcule les returns (pct_change) sur chaque série. Drop la première ligne
    (toujours NaN). Skip toute série dont la valeur initiale est 0 ou négative
    (pct_change indéfini ou trompeur).
    """
    warnings: list[str] = []
    if wide is None or wide.empty:
        return None, ["DataFrame vide, returns impossibles."]

    invalid_cols: list[str] = []
    for col in wide.columns:
        s = wide[col].dropna()
        if s.empty:
            invalid_cols.append(col)
            continue
        # pct_change est sensible aux valeurs proches de zéro ou négatives.
        # Pour des prix/taux financiers, valeurs ≤ 0 sont anomalies — on skip.
        if (s <= 0).any():
            invalid_cols.append(col)

    if invalid_cols:
        warnings.append(
            f"Colonnes ignorées pour le calcul de returns "
            f"(valeurs ≤ 0 présentes) : {invalid_cols}"
        )

    keep = [c for c in wide.columns if c not in invalid_cols]
    if len(keep) < 2:
        return None, warnings + [
            "Moins de 2 séries éligibles aux returns."
        ]

    returns = wide[keep].pct_change().iloc[1:]
    return returns, warnings


def _compute_block(
    wide: pd.DataFrame,
    *,
    block_name: str,
    min_points: int,
) -> tuple[dict[str, Any], list[str]]:
    """
    Calcule Pearson + Spearman pour un bloc (levels ou returns) et extrait
    les top paires + divergences.
    """
    warnings: list[str] = []
    cols = list(wide.columns)

    # Drop des lignes avec au moins un NaN. Pandas .corr() gère les NaN
    # par paire (`min_periods`), mais on préfère une matrice consistante
    # pour que `n_points_used` soit unique et interprétable.
    work = wide.dropna()
    n_used = int(len(work))

    if n_used < 3:
        warnings.append(
            f"Bloc '{block_name}' : {n_used} lignes après dropna — "
            "insuffisant pour calculer une corrélation."
        )
        return _empty_block(cols, n_used), warnings

    if n_used < min_points:
        warnings.append(
            f"Bloc '{block_name}' : {n_used} points utilisés "
            f"(< {min_points} requis). Fiabilité dégradée."
        )

    # Calcul des matrices. `numeric_only=True` est une ceinture-bretelles.
    pearson = work.corr(method="pearson", numeric_only=True)
    spearman = work.corr(method="spearman", numeric_only=True)

    # Extraction des paires (triangle supérieur).
    pairs = _extract_pairs(pearson, spearman, cols)

    # Tri des paires par |pearson| décroissant pour top positive/negative.
    pos_sorted = sorted(
        [p for p in pairs if p["pearson"] is not None and p["pearson"] > 0],
        key=lambda p: p["pearson"],
        reverse=True,
    )[:TOP_PAIRS_PER_DIRECTION]

    neg_sorted = sorted(
        [p for p in pairs if p["pearson"] is not None and p["pearson"] < 0],
        key=lambda p: p["pearson"],
    )[:TOP_PAIRS_PER_DIRECTION]

    strong_count = sum(
        1
        for p in pairs
        if p["pearson"] is not None and abs(p["pearson"]) >= STRONG_THRESHOLD
    )

    # Divergences Pearson vs Spearman (signal de non-linéarité ou outliers).
    divergent: list[dict[str, Any]] = []
    for p in pairs:
        if p["pearson"] is None or p["spearman"] is None:
            continue
        diff = abs(p["pearson"] - p["spearman"])
        if diff >= PEARSON_SPEARMAN_DIVERGENCE_THRESHOLD:
            divergent.append({**p, "abs_diff": round(diff, 4)})

    divergent_sorted = sorted(
        divergent, key=lambda x: x["abs_diff"], reverse=True
    )[:TOP_PAIRS_PER_DIRECTION]

    block: dict[str, Any] = {
        "n_points_used": n_used,
        "series": cols,
        "matrix_pearson": _matrix_to_jsonsafe(pearson),
        "matrix_spearman": _matrix_to_jsonsafe(spearman),
        "all_pairs": pairs,
        "top_positive_pairs": pos_sorted,
        "top_negative_pairs": neg_sorted,
        "strong_pairs_count": strong_count,
        "divergent_pairs": divergent_sorted,
        "thresholds": {
            "strong": STRONG_THRESHOLD,
            "moderate": MODERATE_THRESHOLD,
            "divergence": PEARSON_SPEARMAN_DIVERGENCE_THRESHOLD,
        },
    }
    return block, warnings


def _empty_block(cols: list[str], n_used: int) -> dict[str, Any]:
    """Bloc vide cohérent (utile pour ne pas casser la structure attendue)."""
    return {
        "n_points_used": n_used,
        "series": cols,
        "matrix_pearson": None,
        "matrix_spearman": None,
        "all_pairs": [],
        "top_positive_pairs": [],
        "top_negative_pairs": [],
        "strong_pairs_count": 0,
        "divergent_pairs": [],
        "thresholds": {
            "strong": STRONG_THRESHOLD,
            "moderate": MODERATE_THRESHOLD,
            "divergence": PEARSON_SPEARMAN_DIVERGENCE_THRESHOLD,
        },
    }


def _extract_pairs(
    pearson: pd.DataFrame,
    spearman: pd.DataFrame,
    cols: list[str],
) -> list[dict[str, Any]]:
    """
    Extrait toutes les paires uniques (triangle supérieur strict) avec leurs
    valeurs Pearson et Spearman, plus un label d'intensité.
    """
    out: list[dict[str, Any]] = []
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            a, b = cols[i], cols[j]
            p_val = _safe_float(pearson.iat[i, j])
            s_val = _safe_float(spearman.iat[i, j])
            out.append(
                {
                    "a": a,
                    "b": b,
                    "pearson": p_val,
                    "spearman": s_val,
                    "intensity": _intensity_label(p_val),
                }
            )
    return out


def _intensity_label(corr: float | None) -> str:
    """Étiquette qualitative pour le LLM. Aucune valeur métier hardcodée."""
    if corr is None or math.isnan(corr):
        return "undefined"
    a = abs(corr)
    if a >= STRONG_THRESHOLD:
        return "strong"
    if a >= MODERATE_THRESHOLD:
        return "moderate"
    return "weak"


def _matrix_to_jsonsafe(m: pd.DataFrame) -> dict[str, dict[str, float | None]]:
    """Sérialise une matrice corr en dict-of-dicts JSON-safe."""
    out: dict[str, dict[str, float | None]] = {}
    for r in m.index:
        out[str(r)] = {
            str(c): _safe_float(m.at[r, c]) for c in m.columns
        }
    return out


def _safe_float(x: Any) -> float | None:
    """Convertit en float natif. NaN/inf → None (JSON-safe)."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, 4)


def _build_method_notes(pretreatment: str) -> str:
    """
    Notes méthodologiques destinées au LLM. Aucune valeur métier crypto/macro.
    """
    base = (
        "Pearson mesure la corrélation linéaire, Spearman la corrélation de "
        "rang (monotone). Un écart important entre les deux suggère une "
        "relation non-linéaire ou la présence d'outliers."
    )
    if pretreatment == "levels":
        return (
            base
            + " Analyse sur niveaux bruts uniquement : attention aux "
            "corrélations fallacieuses entre séries non-stationnaires."
        )
    if pretreatment == "returns":
        return (
            base
            + " Analyse sur returns (pct_change) : évite les corrélations "
            "fallacieuses dues aux tendances communes."
        )
    return (
        base
        + " Comparaison levels vs returns : les corrélations sur niveaux "
        "peuvent être trompeuses pour des séries non-stationnaires. "
        "Privilégier l'interprétation des corrélations sur returns."
    )
