"""
app/agents/dataframe_utils.py
Utilitaires de conversion records ↔ DataFrame.

Le SQL Agent retourne un dict JSON sérialisable :
  {"records": [...], "columns": [...], "row_count": int, ...}

L'Analyse Agent (et tout futur composant) utilise ces helpers
pour convertir en DataFrame Pandas quand nécessaire.

Usage :
    from app.agents.dataframe_utils import records_to_dataframe, dataframe_to_records

    # SQL Agent output → DataFrame
    df = records_to_dataframe(sql_result)

    # DataFrame → dict pour cache/API
    result_dict = dataframe_to_records(df)
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def records_to_dataframe(
    sql_result: dict[str, Any],
    parse_dates: bool = True,
) -> pd.DataFrame:
    """
    Convertit la sortie du SQL Agent en DataFrame Pandas.

    Args:
        sql_result: dict avec au minimum "records" et "columns"
        parse_dates: si True, tente de convertir les colonnes "date"
                     en datetime

    Returns:
        pd.DataFrame prêt pour l'analyse

    Raises:
        ValueError: si records est vide ou manquant
    """
    records = sql_result.get("records", [])
    columns = sql_result.get("columns", [])

    if not records:
        # Retourner un DataFrame vide avec les bonnes colonnes
        return pd.DataFrame(columns=columns) if columns else pd.DataFrame()

    df = pd.DataFrame(records)

    # Réordonner les colonnes selon l'ordre attendu
    if columns:
        present_cols = [c for c in columns if c in df.columns]
        extra_cols = [c for c in df.columns if c not in columns]
        df = df[present_cols + extra_cols]

    # Parser les colonnes date — détection flexible par nom
    if parse_dates:
        for col in df.columns:
            if _is_date_column(col):
                try:
                    df[col] = pd.to_datetime(df[col])
                except (ValueError, TypeError):
                    pass

    # Trier par date si présente
    if "date" in df.columns:
        df = df.sort_values("date").reset_index(drop=True)

    logger.info(
        "records_to_dataframe — %d rows, %d cols, dtypes: %s",
        len(df),
        len(df.columns),
        dict(df.dtypes.apply(str)),
    )
    return df


def _is_date_column(col_name: str) -> bool:
    """
    Détecte si un nom de colonne est une colonne date/timestamp.

    Couvre : date, month, timestamp, created_at, updated_at,
    event_date, publish_date, observation_date, etc.
    """
    col = col_name.lower()
    # Match exact
    if col in ("date", "month", "timestamp", "datetime"):
        return True
    # Match par suffixe
    if col.endswith("_date") or col.endswith("_at") or col.endswith("_time"):
        return True
    # Match par préfixe
    if col.startswith("date_") or col.startswith("dt_"):
        return True
    return False


def dataframe_to_records(df: pd.DataFrame) -> dict[str, Any]:
    """
    Convertit un DataFrame en dict sérialisable JSON.

    Utilisé pour le cache Redis et les réponses API.
    Gère la conversion des types Pandas (Timestamp, NaN, etc.)
    en types Python natifs.

    Returns:
        {"records": [...], "columns": [...], "row_count": int}
    """
    df_clean = df.copy()

    # Convertir les Timestamp en strings ISO complètes
    for col in df_clean.columns:
        if pd.api.types.is_datetime64_any_dtype(df_clean[col]):
            # Format ISO complet — si toutes les heures sont 00:00:00,
            # on raccourcit en date seule pour lisibilité
            has_time = (df_clean[col].dt.time != pd.Timestamp("00:00:00").time()).any()
            if has_time:
                df_clean[col] = df_clean[col].dt.strftime("%Y-%m-%dT%H:%M:%S")
            else:
                df_clean[col] = df_clean[col].dt.strftime("%Y-%m-%d")

    # Remplacer NaN par None — astype(object) d'abord pour que
    # les colonnes numériques ne gardent pas des NaN float
    df_clean = df_clean.astype(object).where(pd.notnull(df_clean), None)

    records = df_clean.to_dict("records")

    return {
        "records": records,
        "columns": list(df_clean.columns),
        "row_count": len(records),
    }


def merge_dataframes(
    sql_results: list[dict[str, Any]],
    on: str = "date",
    how: str = "outer",
    suffixes: tuple[str, str] | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """
    Fusionne plusieurs résultats SQL Agent en un seul DataFrame.

    Utile pour les plans comparison et correlation qui produisent
    plusieurs DataFrames à aligner temporellement.

    Args:
        sql_results: liste de dicts SQL Agent
        on: colonne de jointure (défaut: "date")
        how: type de jointure (défaut: "outer")
        suffixes: suffixes pour les colonnes dupliquées

    Returns:
        Tuple (DataFrame fusionné, liste des warnings)
        Les warnings incluent les sources vides pour la traçabilité.
    """
    warnings: list[str] = []
    dfs: list[pd.DataFrame] = []

    for i, result in enumerate(sql_results):
        step_id = result.get("step_id", f"source_{i}")
        records = result.get("records", [])

        if not records:
            warnings.append(
                f"Source '{step_id}' a retourné 0 résultats — "
                f"exclue de la fusion"
            )
            continue

        dfs.append(records_to_dataframe(result))

    if not dfs:
        warnings.append("Toutes les sources sont vides — DataFrame vide retourné")
        return pd.DataFrame(), warnings

    if len(dfs) == 1:
        return dfs[0], warnings

    result = dfs[0]
    for i, df in enumerate(dfs[1:], start=1):
        suf = suffixes or (f"_{i-1}", f"_{i}")
        if on in result.columns and on in df.columns:
            result = result.merge(df, on=on, how=how, suffixes=suf)
        else:
            result = pd.concat([result, df], ignore_index=True)

    if "date" in result.columns:
        result = result.sort_values("date").reset_index(drop=True)

    logger.info(
        "merge_dataframes — %d sources → %d rows, %d cols, %d warnings",
        len(dfs),
        len(result),
        len(result.columns),
        len(warnings),
    )
    return result, warnings