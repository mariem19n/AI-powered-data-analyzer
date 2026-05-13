"""
app/agents/analysis/stats/anomaly.py

Détection d'anomalies sur des séries numériques ou temporelles.

Deux algorithmes :
  - IQR (interquartile range) : robuste, résiste aux outliers
    Anomalie : valeur hors de [Q1 - 1.5*IQR, Q3 + 1.5*IQR]
    À utiliser par défaut.
  - Z-score : fallback pour les petites séries (< 20 points) où l'IQR
    n'est pas fiable, ou quand on veut une seconde méthode pour comparer.
    Anomalie : abs(value - mean) > 3 * std

Toutes les fonctions sont PURES (pas d'effets de bord, pas de logging).
Elles retournent des dicts JSON-safe directement utilisables par le LLM
et la couche viz.

Aucun nom de colonne en dur — les fonctions reçoivent les colonnes en paramètre.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.agents.analysis.stats.descriptive import _safe_number, _to_iso

# ─── Constantes algorithmiques ────────────────────────────────────────────

# Seuil sous lequel on bascule sur Z-score plutôt que sur IQR.
# Avec moins de ~20 points, les quartiles sont peu fiables.
SMALL_SAMPLE_THRESHOLD = 20

# Multiplicateur IQR — 1.5 est la convention standard de Tukey.
# Augmenter à 3.0 pour ne capturer que les "extreme outliers".
IQR_MULTIPLIER = 1.5

# Seuil Z-score — 3.0 = 99.7% des points sous distribution normale.
ZSCORE_THRESHOLD = 3.0

# Contamination par défaut pour Isolation Forest — 5% est la valeur
# standard pour des données financières (les anomalies sont rares mais
# pas négligeables). Ajustable via paramètre de la fonction.
ISOLATION_FOREST_CONTAMINATION = 0.05

# Nombre minimum de colonnes pour déclencher Isolation Forest.
# 1 colonne = univarié = IQR/Z-score sont mieux adaptés.
# 2+ colonnes = multivarié = Isolation Forest devient pertinent.
ISOLATION_FOREST_MIN_COLS = 2

# Nombre minimum de points pour Isolation Forest.
# En dessous de 30, l'algorithme est instable.
ISOLATION_FOREST_MIN_POINTS = 30


# ─── Helper : conversion sûre d'une Series ────────────────────────────────


def _clean_numeric_series(
    series: pd.Series,
) -> tuple[pd.Series, pd.Series]:
    """
    Sépare valeurs valides / NaN dans une Series numérique.

    Returns:
        (values, original_index_of_values) — la valeur et son index dans
        la Series originale (pour pouvoir reconstruire date_col par index).
    """
    mask = series.notna()
    return series[mask], series[mask].index


# ─── Détection IQR ────────────────────────────────────────────────────────


def detect_anomalies_iqr(
    df: pd.DataFrame,
    value_col: str,
    date_col: str | None = None,
    multiplier: float = IQR_MULTIPLIER,
) -> dict[str, Any]:
    """
    Détecte les anomalies par méthode IQR (interquartile range).

    Robuste aux outliers eux-mêmes : un gros outlier ne fausse pas Q1/Q3.
    Recommandé par défaut sur des distributions financières (prix, volume)
    qui sont rarement gaussiennes.

    Args:
        df : DataFrame source
        value_col : nom de la colonne numérique à analyser
        date_col : nom de la colonne date (optionnel) pour annoter les
                   anomalies avec leur date — utile pour la viz.
        multiplier : multiplicateur de l'IQR (1.5 = standard Tukey,
                     3.0 = extreme outliers seulement).

    Returns:
        dict avec :
          - method : "iqr"
          - column : nom de la colonne analysée
          - n_points : nombre de points analysés
          - n_anomalies : nombre d'anomalies détectées
          - anomaly_rate : taux en pourcentage
          - thresholds : {lower, upper} bornes utilisées
          - quartiles : {q1, q3, iqr}
          - anomalies : liste de dicts par anomalie
              {value, date (iso), deviation_from_bound, direction}
              triée par |deviation| décroissante
    """
    if value_col not in df.columns:
        return _empty_result("iqr", value_col, reason=f"colonne '{value_col}' absente")

    series = df[value_col]
    if not pd.api.types.is_numeric_dtype(series):
        return _empty_result(
            "iqr", value_col, reason=f"colonne '{value_col}' non numérique"
        )

    values, original_idx = _clean_numeric_series(series)
    n = len(values)

    if n < 4:
        # Pas assez de points pour calculer Q1 et Q3 de manière sensée.
        return _empty_result(
            "iqr",
            value_col,
            reason=f"trop peu de points ({n} < 4) pour IQR",
            n_points=n,
        )

    q1 = float(np.quantile(values, 0.25))
    q3 = float(np.quantile(values, 0.75))
    iqr = q3 - q1

    # Cas dégénéré : tous les points sont identiques → IQR = 0 → pas d'anomalies.
    if iqr == 0:
        return {
            "method": "iqr",
            "column": value_col,
            "n_points": n,
            "n_anomalies": 0,
            "anomaly_rate": 0.0,
            "thresholds": {"lower": q1, "upper": q3},
            "quartiles": {"q1": q1, "q3": q3, "iqr": 0.0},
            "anomalies": [],
            "warnings": ["IQR=0 (toutes les valeurs identiques)"],
        }

    lower = q1 - multiplier * iqr
    upper = q3 + multiplier * iqr

    anomalies = _build_anomaly_records_iqr(
        df=df,
        values=values,
        original_idx=original_idx,
        lower=lower,
        upper=upper,
        date_col=date_col,
        value_col=value_col,
    )

    n_anomalies = len(anomalies)
    return {
        "method": "iqr",
        "column": value_col,
        "n_points": n,
        "n_anomalies": n_anomalies,
        "anomaly_rate": round(n_anomalies / n * 100, 2) if n > 0 else 0.0,
        "thresholds": {
            "lower": _safe_number(lower),
            "upper": _safe_number(upper),
        },
        "quartiles": {
            "q1": _safe_number(q1),
            "q3": _safe_number(q3),
            "iqr": _safe_number(iqr),
        },
        "multiplier": multiplier,
        "anomalies": anomalies,
        "warnings": [],
    }


def _build_anomaly_records_iqr(
    *,
    df: pd.DataFrame,
    values: pd.Series,
    original_idx: pd.Index,
    lower: float,
    upper: float,
    date_col: str | None,
    value_col: str,
) -> list[dict[str, Any]]:
    """Construit la liste d'anomalies au format JSON-safe."""
    anomaly_mask_low = values < lower
    anomaly_mask_high = values > upper
    anomaly_mask = anomaly_mask_low | anomaly_mask_high

    anomaly_indices = original_idx[anomaly_mask]
    if len(anomaly_indices) == 0:
        return []

    records: list[dict[str, Any]] = []
    for idx in anomaly_indices:
        v = float(values.loc[idx])
        if v < lower:
            deviation = lower - v
            direction = "below"
        else:
            deviation = v - upper
            direction = "above"

        record: dict[str, Any] = {
            "value": _safe_number(v),
            "deviation": _safe_number(deviation),
            "direction": direction,
            "row_index": int(idx) if hasattr(idx, "__index__") else None,
        }
        if date_col and date_col in df.columns:
            record["date"] = _to_iso(df[date_col].iloc[df.index.get_loc(idx)])
        records.append(record)

    # Tri par déviation décroissante (les plus extrêmes d'abord)
    records.sort(key=lambda r: r["deviation"] or 0, reverse=True)
    return records


# ─── Détection Z-score ────────────────────────────────────────────────────


def detect_anomalies_zscore(
    df: pd.DataFrame,
    value_col: str,
    date_col: str | None = None,
    threshold: float = ZSCORE_THRESHOLD,
) -> dict[str, Any]:
    """
    Détecte les anomalies par Z-score (écart à la moyenne en unités d'écart-type).

    Plus rapide et plus simple que l'IQR, mais sensible aux outliers
    eux-mêmes. À privilégier quand la distribution est proche de la normale
    et qu'il y a peu de points.

    Args:
        threshold : nombre d'écarts-types au-delà duquel un point est anomalie.
                    3.0 = 99.7% des points en distribution normale.
    """
    if value_col not in df.columns:
        return _empty_result("zscore", value_col, reason=f"colonne '{value_col}' absente")

    series = df[value_col]
    if not pd.api.types.is_numeric_dtype(series):
        return _empty_result(
            "zscore", value_col, reason=f"colonne '{value_col}' non numérique"
        )

    values, original_idx = _clean_numeric_series(series)
    n = len(values)

    if n < 3:
        return _empty_result(
            "zscore",
            value_col,
            reason=f"trop peu de points ({n} < 3) pour Z-score",
            n_points=n,
        )

    mean = float(values.mean())
    std = float(values.std(ddof=1))

    if std == 0 or np.isnan(std):
        return {
            "method": "zscore",
            "column": value_col,
            "n_points": n,
            "n_anomalies": 0,
            "anomaly_rate": 0.0,
            "mean": _safe_number(mean),
            "std": 0.0,
            "threshold": threshold,
            "anomalies": [],
            "warnings": ["std=0 (toutes les valeurs identiques)"],
        }

    z_scores = (values - mean) / std
    anomaly_mask = z_scores.abs() > threshold
    anomaly_indices = original_idx[anomaly_mask]

    records: list[dict[str, Any]] = []
    for idx in anomaly_indices:
        v = float(values.loc[idx])
        z = float(z_scores.loc[idx])
        record: dict[str, Any] = {
            "value": _safe_number(v),
            "z_score": _safe_number(z),
            "deviation": _safe_number(abs(z)),
            "direction": "above" if z > 0 else "below",
            "row_index": int(idx) if hasattr(idx, "__index__") else None,
        }
        if date_col and date_col in df.columns:
            record["date"] = _to_iso(df[date_col].iloc[df.index.get_loc(idx)])
        records.append(record)

    # Tri par |z| décroissant
    records.sort(key=lambda r: r["deviation"] or 0, reverse=True)

    n_anomalies = len(records)
    return {
        "method": "zscore",
        "column": value_col,
        "n_points": n,
        "n_anomalies": n_anomalies,
        "anomaly_rate": round(n_anomalies / n * 100, 2) if n > 0 else 0.0,
        "mean": _safe_number(mean),
        "std": _safe_number(std),
        "threshold": threshold,
        "anomalies": records,
        "warnings": [],
    }


# ─── Sélection automatique ────────────────────────────────────────────────


def detect_anomalies_auto(
    df: pd.DataFrame,
    value_col: str | list[str],
    date_col: str | None = None,
    small_sample_threshold: int = SMALL_SAMPLE_THRESHOLD,
) -> dict[str, Any]:
    """
    Choisit automatiquement l'algorithme :
      - Si plusieurs colonnes (list) ET n >= 30 → Isolation Forest (multivarié)
      - Si 1 colonne ET n >= small_sample_threshold (défaut 20) → IQR
      - Sinon → Z-score

    Args:
        value_col : soit une str (analyse univariée) soit une list[str]
                    (analyse multivariée → Isolation Forest).

    Ajoute le champ `auto_selected` dans le résultat pour traçabilité.
    """
    # Normalisation : toujours travailler avec une liste en interne.
    if isinstance(value_col, str):
        cols_list = [value_col]
    else:
        cols_list = list(value_col)

    if not cols_list:
        return _empty_result(
            "auto", "<aucune>", reason="aucune colonne fournie"
        )

    # Vérification de l'existence des colonnes.
    missing = [c for c in cols_list if c not in df.columns]
    if missing:
        return _empty_result(
            "auto",
            ",".join(cols_list),
            reason=f"colonnes absentes : {missing}",
        )

    # Cas 1 : multi-colonnes → Isolation Forest si assez de points.
    if len(cols_list) >= ISOLATION_FOREST_MIN_COLS:
        n_valid = int(df[cols_list].dropna().shape[0])
        if n_valid >= ISOLATION_FOREST_MIN_POINTS:
            result = detect_anomalies_isolation_forest(
                df, cols_list, date_col=date_col
            )
            result["auto_selected"] = "isolation_forest"
            result["auto_reason"] = (
                f"{len(cols_list)} colonnes numériques + n={n_valid} ≥ "
                f"{ISOLATION_FOREST_MIN_POINTS} → analyse multivariée"
            )
            return result
        # Trop peu de points : on retombe sur IQR de la 1re colonne.
        primary = cols_list[0]
        result = detect_anomalies_iqr(df, primary, date_col=date_col)
        result["auto_selected"] = "iqr"
        result["auto_reason"] = (
            f"multi-colonnes mais n={n_valid} < {ISOLATION_FOREST_MIN_POINTS} → "
            f"fallback univarié IQR sur '{primary}'"
        )
        return result

    # Cas 2 : 1 seule colonne → IQR ou Z-score selon n.
    primary = cols_list[0]
    n_valid = int(df[primary].notna().sum())

    if n_valid >= small_sample_threshold:
        result = detect_anomalies_iqr(df, primary, date_col=date_col)
        result["auto_selected"] = "iqr"
        result["auto_reason"] = (
            f"1 colonne, n={n_valid} >= seuil {small_sample_threshold} → IQR"
        )
    else:
        result = detect_anomalies_zscore(df, primary, date_col=date_col)
        result["auto_selected"] = "zscore"
        result["auto_reason"] = (
            f"1 colonne, n={n_valid} < seuil {small_sample_threshold} → Z-score"
        )

    return result


# ─── Détection Isolation Forest (multivariée) ─────────────────────────────


def detect_anomalies_isolation_forest(
    df: pd.DataFrame,
    value_cols: list[str],
    date_col: str | None = None,
    contamination: float = ISOLATION_FOREST_CONTAMINATION,
    random_state: int = 42,
) -> dict[str, Any]:
    """
    Détecte les anomalies par Isolation Forest (algorithme multivarié).

    Capable de détecter des anomalies qui ne le seraient pas si on
    regardait chaque colonne séparément. Exemple : un jour où le prix
    BTC chute légèrement (pas anormal) ET le volume explose (pas
    anormal non plus) ET le spread s'élargit (pas anormal) — la
    combinaison des 3 EST une anomalie que l'IQR univarié manquerait.

    Args:
        df : DataFrame source
        value_cols : liste des colonnes numériques à analyser ensemble
        date_col : nom de la colonne date (optionnelle, pour annoter)
        contamination : taux d'anomalies attendu (0 < c < 0.5).
                        5% est standard pour données financières.
        random_state : seed pour reproductibilité

    Returns:
        dict avec :
          - method : "isolation_forest"
          - columns : liste des colonnes analysées
          - n_points : nombre de points analysés
          - n_anomalies : nombre détecté
          - anomaly_rate : taux en %
          - contamination : valeur utilisée
          - anomalies : liste de dicts par anomalie. Chaque anomalie
                        a un dict `values` (col → val) plutôt qu'un
                        `value` scalaire (cas univarié).
                        {values, anomaly_score, date (iso), row_index}
                        triée par |anomaly_score| décroissant
                        (les plus fortes d'abord).
    """
    # ─── Validation des colonnes ──────────────────────────────────────
    if not value_cols:
        return _empty_result(
            "isolation_forest", "<aucune>",
            reason="aucune colonne fournie",
        )

    missing = [c for c in value_cols if c not in df.columns]
    if missing:
        return _empty_result(
            "isolation_forest",
            ",".join(value_cols),
            reason=f"colonnes absentes : {missing}",
        )

    non_numeric = [
        c for c in value_cols
        if not pd.api.types.is_numeric_dtype(df[c])
    ]
    if non_numeric:
        return _empty_result(
            "isolation_forest",
            ",".join(value_cols),
            reason=f"colonnes non numériques : {non_numeric}",
        )

    # ─── Préparation : on ne garde que les lignes complètes ───────────
    df_clean = df[value_cols].dropna()
    n = len(df_clean)

    if n < ISOLATION_FOREST_MIN_POINTS:
        return _empty_result(
            "isolation_forest",
            ",".join(value_cols),
            reason=(
                f"trop peu de points complets ({n} < "
                f"{ISOLATION_FOREST_MIN_POINTS}) pour Isolation Forest"
            ),
            n_points=n,
        )

    # ─── Fit + prédiction ─────────────────────────────────────────────
    try:
        from sklearn.ensemble import IsolationForest
    except ImportError:
        return _empty_result(
            "isolation_forest",
            ",".join(value_cols),
            reason="scikit-learn non disponible",
            n_points=n,
        )

    # n_estimators=100 est la valeur par défaut, suffisante pour
    # nos volumes (quelques centaines à quelques milliers de points).
    model = IsolationForest(
        contamination=contamination,
        random_state=random_state,
        n_estimators=100,
    )
    predictions = model.fit_predict(df_clean.values)
    # decision_function : score négatif = plus probable d'être anomalie
    # On transforme en score positif pour les anomalies (plus c'est haut,
    # plus c'est anormal) — plus intuitif côté UI.
    raw_scores = model.decision_function(df_clean.values)
    anomaly_scores = -raw_scores  # inverser : positif = anomalie

    # ─── Extraction des anomalies ─────────────────────────────────────
    anomaly_mask = predictions == -1
    anomaly_indices = df_clean.index[anomaly_mask]

    records: list[dict[str, Any]] = []
    for idx in anomaly_indices:
        # Construction du dict de valeurs par colonne pour cette anomalie.
        values_dict = {
            col: _safe_number(float(df_clean.loc[idx, col]))
            for col in value_cols
        }
        score = float(anomaly_scores[df_clean.index.get_loc(idx)])

        record: dict[str, Any] = {
            "values": values_dict,
            "anomaly_score": _safe_number(score),
            "row_index": int(idx) if hasattr(idx, "__index__") else None,
        }
        if date_col and date_col in df.columns:
            record["date"] = _to_iso(df[date_col].iloc[df.index.get_loc(idx)])
        records.append(record)

    # Tri par score décroissant (les plus anormales d'abord).
    records.sort(key=lambda r: r["anomaly_score"] or 0, reverse=True)

    n_anomalies = len(records)
    return {
        "method": "isolation_forest",
        "columns": list(value_cols),
        "n_points": n,
        "n_anomalies": n_anomalies,
        "anomaly_rate": round(n_anomalies / n * 100, 2) if n > 0 else 0.0,
        "contamination": contamination,
        "anomalies": records,
        "warnings": [],
    }


# ─── Helper : résultat vide standardisé ───────────────────────────────────


def _empty_result(
    method: str,
    column: str,
    *,
    reason: str,
    n_points: int = 0,
) -> dict[str, Any]:
    """Format standardisé pour un résultat vide / non calculable."""
    return {
        "method": method,
        "column": column,
        "n_points": n_points,
        "n_anomalies": 0,
        "anomaly_rate": 0.0,
        "anomalies": [],
        "warnings": [f"Détection impossible : {reason}"],
    }