"""
app/agents/analysis/tasks/anomaly_detection.py

Task : détection d'anomalies sur des séries numériques ou temporelles.

Sélection de la colonne cible (par ordre de priorité) :
  1. SemanticContext.columns[].column si présent → utilise les colonnes
     que le Semantic Layer a résolues
  2. Toutes les colonnes numériques du DataFrame en fallback

Algorithme : IQR par défaut, Z-score automatiquement pour les petits
échantillons (< 20 points). Configurable via instruction["force_method"].

Shapes supportées :
  - timeseries : cas principal, viz avec points anomalies marqués
  - numeric_only : sans date, on génère une viz simple si peu d'anomalies
  - groupby : non supporté (warning explicite, on retourne 0 anomalie)

Sortie :
  - insights NL en français
  - viz Plotly (line_chart timeseries + marker_overlays rouges)
  - metadata.anomalies_table : liste structurée pour l'UI / API
  - stats : résumé technique (n_anomalies, méthode, seuils)
  - kg_payload : VIDE pour ce ticket (KG branché plus tard)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

import pandas as pd

from app.agents.analysis.llm.insight_generator import (
    GeneratedInsights,
    InsightGenerator,
)
from app.agents.analysis.enrichment.gdelt import (
    extract_entity_name_from_context,
    fetch_gdelt_context,
)
from app.agents.analysis.stats.anomaly import (
    SMALL_SAMPLE_THRESHOLD,
    detect_anomalies_auto,
    detect_anomalies_iqr,
    detect_anomalies_zscore,
)
from app.agents.analysis.stats.descriptive import (
    detect_dataframe_shape,
)
from app.agents.analysis.tasks.base import (
    AnalysisTask,
    TaskResult,
    register_task,
)
from app.agents.analysis.viz.templates import default_viz_for_shape

logger = logging.getLogger(__name__)


EMPTY_RESULT_CONFIDENCE = 0.3


# ─── Sélection de la colonne cible ────────────────────────────────────────


def _select_value_columns(
    df: pd.DataFrame,
    shape_info: dict[str, Any],
    semantic_context: dict[str, Any] | None,
) -> tuple[list[str], list[str]]:
    """
    Détermine quelles colonnes analyser pour la détection d'anomalies.

    Priorité 1 : SemanticContext.columns[*].column → utilise toutes les
                 colonnes que le Semantic Layer a résolues
    Priorité 2 : toutes les colonnes numériques du DataFrame (fallback)

    NOTE : on retourne TOUTES les colonnes pertinentes (pas seulement la
    première) parce qu'avec Isolation Forest, l'analyse multi-colonnes
    apporte plus de valeur. La task décidera ensuite de basculer vers
    l'algo univarié ou multivarié via detect_anomalies_auto.

    Returns:
        (selected_columns, warnings)
    """
    warnings: list[str] = []
    numeric_cols = shape_info.get("numeric_cols", [])

    if not numeric_cols:
        warnings.append(
            "anomaly_detection: aucune colonne numérique dans le DataFrame"
        )
        return [], warnings

    # Priorité au SemanticContext.
    semantic_cols = _extract_semantic_columns(semantic_context)
    if semantic_cols:
        # On garde toutes celles qui existent ET sont numériques.
        validated = [c for c in semantic_cols if c in numeric_cols]
        if validated:
            return validated, warnings

        warnings.append(
            f"anomaly_detection: colonnes du SemanticContext absentes "
            f"ou non numériques ({semantic_cols}) — fallback sur toutes les "
            f"colonnes numériques"
        )

    # Fallback : toutes les numériques.
    return list(numeric_cols), warnings


def _extract_semantic_columns(
    semantic_context: dict[str, Any] | None,
) -> list[str]:
    """Extrait les noms de colonnes techniques depuis SemanticContext."""
    if not isinstance(semantic_context, dict):
        return []
    cols_meta = semantic_context.get("columns")
    if not isinstance(cols_meta, list):
        return []
    return [
        str(c.get("column"))
        for c in cols_meta
        if isinstance(c, dict) and c.get("column")
    ]


def _select_date_column(shape_info: dict[str, Any]) -> str | None:
    """Retourne la première colonne datetime, ou None."""
    datetime_cols = shape_info.get("datetime_cols", [])
    return datetime_cols[0] if datetime_cols else None


# ─── Choix de l'algorithme ────────────────────────────────────────────────


def _run_detection(
    df: pd.DataFrame,
    value_cols: list[str],
    date_col: str | None,
    force_method: str | None,
) -> dict[str, Any]:
    """
    Exécute la détection avec l'algo demandé ou en sélection auto.

    force_method : "iqr" | "zscore" | "isolation_forest" | None (= auto)
    value_cols : liste de colonnes. Si 1 seule + force_method en {iqr,zscore}
                 → univarié. Si 2+ + force_method=isolation_forest → multivarié.
                 Sinon, detect_anomalies_auto décide.
    """
    if force_method == "iqr":
        # Univarié forcé : on prend la première colonne uniquement.
        primary = value_cols[0]
        result = detect_anomalies_iqr(df, primary, date_col=date_col)
        result["auto_selected"] = "iqr"
        result["auto_reason"] = "forcé par instruction"
        return result
    if force_method == "zscore":
        primary = value_cols[0]
        result = detect_anomalies_zscore(df, primary, date_col=date_col)
        result["auto_selected"] = "zscore"
        result["auto_reason"] = "forcé par instruction"
        return result
    if force_method == "isolation_forest":
        from app.agents.analysis.stats.anomaly import (
            detect_anomalies_isolation_forest,
        )
        result = detect_anomalies_isolation_forest(
            df, value_cols, date_col=date_col
        )
        result["auto_selected"] = "isolation_forest"
        result["auto_reason"] = "forcé par instruction"
        return result

    # Mode auto : detect_anomalies_auto sait gérer str ou list[str].
    return detect_anomalies_auto(df, value_cols, date_col=date_col)


# ─── Construction de la viz ───────────────────────────────────────────────


def _build_anomaly_viz(
    df: pd.DataFrame,
    value_col: str,
    date_col: str | None,
    detection_result: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Construit une viz timeseries avec les points anomalies en rouge.

    Pour numeric_only (pas de date_col), pas de viz dans cette version
    (un scatter sans axe temporel est de valeur limitée).

    Returns:
        (visualizations_list, warnings)
    """
    warnings: list[str] = []

    # Sans date_col, on n'a pas vraiment de viz utile pour les anomalies.
    if date_col is None:
        warnings.append(
            "anomaly_detection: pas de viz pour shape numeric_only "
            "(pas de colonne datetime)"
        )
        return [], warnings

    viz_fn = default_viz_for_shape("timeseries")
    if viz_fn is None:
        warnings.append(
            "anomaly_detection: aucune viz par défaut enregistrée pour timeseries"
        )
        return [], warnings

    # Construction des marker overlays : un point rouge par anomalie.
    anomalies = detection_result.get("anomalies", [])
    marker_overlays = _build_marker_overlays(anomalies, value_col, date_col)

    config = {
        "x_col": date_col,
        "y_cols": [value_col],
        "marker_overlays": marker_overlays,
    }

    try:
        viz_dict = viz_fn(df, config)
        return [viz_dict], warnings
    except Exception as e:  # noqa: BLE001
        warnings.append(
            f"anomaly_detection: échec construction viz ({type(e).__name__}: {e})"
        )
        return [], warnings


def _build_marker_overlays(
    anomalies: list[dict[str, Any]],
    value_col: str,
    date_col: str,
) -> list[dict[str, Any]]:
    """
    Construit la liste de marker_overlays attendue par line_chart.py.

    Contrat de line_chart._build_marker_overlays :
      - x_values : liste des dates des anomalies
      - y_values : liste des valeurs correspondantes
      - label : nom affiché dans la légende
      - color : couleur des marqueurs (sinon theme.colors.anomaly)
      - size : taille des marqueurs (défaut 10)
      - series : nom de la série y_col associée (cosmétique)
    """
    if not anomalies:
        return []

    xs: list[Any] = []
    ys: list[Any] = []
    for a in anomalies:
        d = a.get("date")
        v = a.get("value")
        if d is not None and v is not None:
            xs.append(d)
            ys.append(v)

    if not xs:
        return []

    return [
        {
            "x_values": xs,
            "y_values": ys,
            "label": f"Anomalies ({len(xs)})",
            "size": 10,
            "series": value_col,
            # Pas de "color" : on laisse line_chart.py utiliser theme.colors.anomaly
        }
    ]


# ─── Construction de la viz MULTIVARIÉE (Isolation Forest) ────────────────


def _build_anomaly_viz_multivariate(
    df: pd.DataFrame,
    value_cols: list[str],
    date_col: str | None,
    detection_result: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Construit la viz pour les anomalies multivariées détectées par
    Isolation Forest.

    Stratégie :
      - 2 colonnes exactement → scatter plot 2D avec anomalies en rouge
      - 3+ colonnes → une line_chart par colonne avec les anomalies
        marquées sur chacune (toutes les viz partagent les mêmes dates)
    """
    warnings: list[str] = []
    anomalies = detection_result.get("anomalies", [])

    if len(value_cols) == 2:
        return _build_scatter_2d(df, value_cols, date_col, anomalies, warnings)

    return _build_multi_line_charts(df, value_cols, date_col, anomalies, warnings)


def _build_scatter_2d(
    df: pd.DataFrame,
    value_cols: list[str],
    date_col: str | None,
    anomalies: list[dict[str, Any]],
    warnings: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Scatter 2D avec anomalies en surcouche rouge."""
    from app.agents.analysis.viz.templates import get_viz

    scatter_fn = get_viz("scatter")
    if scatter_fn is None:
        warnings.append(
            "anomaly_detection: viz 'scatter' non enregistrée — "
            "fallback sur multi-line_chart"
        )
        return _build_multi_line_charts(
            df, value_cols, date_col, anomalies, warnings
        )

    x_col, y_col = value_cols[0], value_cols[1]

    # Markers d'anomalies au format scatter.
    anomaly_markers: list[dict[str, Any]] = []
    for a in anomalies:
        values = a.get("values", {})
        if x_col not in values or y_col not in values:
            continue
        marker: dict[str, Any] = {
            "x": values[x_col],
            "y": values[y_col],
            "score": a.get("anomaly_score", 0.0),
        }
        if "date" in a:
            marker["label"] = str(a["date"])[:10]
        anomaly_markers.append(marker)

    config = {
        "x_col": x_col,
        "y_col": y_col,
        "anomaly_markers": anomaly_markers,
        "hover_col": date_col,
    }

    try:
        viz_dict = scatter_fn(df, config)
        return [viz_dict], warnings
    except Exception as e:  # noqa: BLE001
        warnings.append(
            f"anomaly_detection: échec scatter 2D ({type(e).__name__}: {e})"
        )
        return [], warnings


def _build_multi_line_charts(
    df: pd.DataFrame,
    value_cols: list[str],
    date_col: str | None,
    anomalies: list[dict[str, Any]],
    warnings: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Une line_chart par colonne avec les anomalies marquées."""
    if date_col is None:
        warnings.append(
            "anomaly_detection: pas de date_col — multi-line_chart impossible"
        )
        return [], warnings

    viz_fn = default_viz_for_shape("timeseries")
    if viz_fn is None:
        warnings.append(
            "anomaly_detection: aucune viz par défaut pour timeseries"
        )
        return [], warnings

    viz_list: list[dict[str, Any]] = []
    for col in value_cols:
        # Collecte des dates et valeurs des anomalies pour cette colonne.
        xs = [a["date"] for a in anomalies
              if "date" in a and a.get("values", {}).get(col) is not None]
        ys = [a["values"][col] for a in anomalies
              if "date" in a and a.get("values", {}).get(col) is not None]

        if xs:
            overlays = [{
                "x_values": xs,
                "y_values": ys,
                "label": f"Anomalies ({len(xs)})",
                "size": 10,
                "series": col,
            }]
        else:
            overlays = []

        config = {
            "x_col": date_col,
            "y_cols": [col],
            "marker_overlays": overlays,
            "title": f"{col} — anomalies multivariées",
        }
        try:
            viz_list.append(viz_fn(df, config))
        except Exception as e:  # noqa: BLE001
            warnings.append(
                f"anomaly_detection: échec viz '{col}' "
                f"({type(e).__name__}: {e})"
            )

    return viz_list, warnings


# ─── La task ──────────────────────────────────────────────────────────────


@register_task
class AnomalyDetectionTask(AnalysisTask):
    """
    Task de détection d'anomalies.

    Consomme un DataFrame upstream (typiquement du SQL Agent), détecte les
    points anormaux selon IQR ou Z-score, et produit insights + viz.
    """

    task_name = "anomaly_detection"

    def __init__(
        self,
        insight_generator: InsightGenerator | None = None,
        db_session_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._insight_generator: InsightGenerator | None = insight_generator
        # Factory injectée par l'app pour ouvrir une connexion read-only
        # à PostgreSQL pour l'enrichissement GDELT. Si None,
        # l'enrichissement est silencieusement sauté.
        self._db_session_factory: Callable[[], Any] | None = db_session_factory

    def set_insight_generator(self, generator: InsightGenerator) -> None:
        self._insight_generator = generator

    def set_db_session_factory(
        self, factory: Callable[[], Any]
    ) -> None:
        """Permet à l'app FastAPI d'injecter le factory au démarrage."""
        self._db_session_factory = factory

    def run(
        self,
        df: pd.DataFrame,
        instruction: dict[str, Any],
        semantic_context: dict[str, Any] | None = None,
        **_unused: Any,  # ← absorber tout kwarg supplémentaire du runner
    ) -> TaskResult:
        start_time = time.perf_counter()
        warnings: list[str] = []

        # 1. Détection de la shape du DataFrame.
        shape_info = detect_dataframe_shape(df)
        shape = shape_info["shape"]

        # 2. groupby non supporté.
        if shape == "groupby":
            warnings.append(
                "anomaly_detection: shape 'groupby' non supportée — "
                "utilise un DataFrame timeseries ou numeric_only"
            )
            return self._build_empty_result(
                start_time, warnings, n_rows=int(len(df))
            )

        # 3. DataFrame vide.
        if shape == "empty" or len(df) == 0:
            warnings.append("anomaly_detection: DataFrame vide ou non analysable")
            return self._build_empty_result(start_time, warnings, n_rows=0)

        # 4. Sélection des colonnes cibles.
        value_cols, sel_warnings = _select_value_columns(
            df, shape_info, semantic_context
        )
        warnings.extend(sel_warnings)
        if not value_cols:
            return self._build_empty_result(
                start_time, warnings, n_rows=int(len(df))
            )

        date_col = _select_date_column(shape_info)
        force_method = instruction.get("force_method")  # "iqr" | "zscore" | "isolation_forest" | None

        # 5. Détection. detect_anomalies_auto choisit l'algo selon le
        #    nombre de colonnes :
        #      - 1 colonne (ou liste à 1 élément) : IQR/Z-score (univarié)
        #      - 2+ colonnes : Isolation Forest (multivarié)
        #    Le résultat indique l'algo utilisé via `auto_selected`.
        detection = _run_detection(df, value_cols, date_col, force_method)
        warnings.extend(detection.get("warnings", []))

        method_used = detection.get("auto_selected") or detection.get("method")
        is_multivariate = method_used == "isolation_forest"

        # 6. Construction de la viz selon univarié / multivarié.
        if is_multivariate:
            viz_list, viz_warnings = _build_anomaly_viz_multivariate(
                df, value_cols, date_col, detection
            )
        else:
            primary_col = (
                detection.get("column")
                or value_cols[0]
            )
            viz_list, viz_warnings = _build_anomaly_viz(
                df, primary_col, date_col, detection
            )
        warnings.extend(viz_warnings)

        # 7. Tableau structuré d'anomalies à exposer dans la réponse.
        anomalies_table = self._build_anomalies_table(
            detection, is_multivariate=is_multivariate
        )

        # 7.5. ENRICHISSEMENT GDELT — articles publiés aux dates d'anomalies.
        # On ne déclenche que s'il y a effectivement des anomalies (sinon
        # rien à expliquer) ET qu'on a une factory DB injectée.
        gdelt_articles, gdelt_warnings = self._fetch_gdelt_enrichment(
            detection=detection,
            df=df,
            semantic_context=semantic_context,
        )
        warnings.extend(gdelt_warnings)

        # 8. Génération des insights NL.
        generated = self._generate_insights(
            detection=detection,
            value_cols=value_cols,
            date_col=date_col,
            n_rows=int(len(df)),
            shape=shape,
            semantic_context=semantic_context,
            warnings_so_far=warnings,
            gdelt_articles=gdelt_articles,
            is_multivariate=is_multivariate,
        )
        warnings.extend(generated.warnings)

        # 9. Assemblage du TaskResult.
        duration_ms = int((time.perf_counter() - start_time) * 1000)

        metadata: dict[str, Any] = {
            "task": self.task_name,
            "subtype": shape,  # "timeseries" ou "numeric_only"
            "confidence": generated.overall_confidence,
            "n_rows": int(len(df)),
            "method": method_used,
            "duration_ms": duration_ms,
            "fallback_used": generated.used_fallback,
            "columns_analyzed": value_cols if is_multivariate else value_cols[:1],
            "is_multivariate": is_multivariate,
            "n_anomalies": detection.get("n_anomalies", 0),
            "anomaly_rate": detection.get("anomaly_rate", 0.0),
            "auto_reason": detection.get("auto_reason"),
            "anomalies_table": anomalies_table,  # tableau structuré pour UI
            "gdelt_articles": gdelt_articles,    # articles d'enrichissement
            "gdelt_enrichment_used": bool(gdelt_articles),
        }
        if generated.llm_metadata:
            metadata["llm"] = generated.llm_metadata

        # Stats détaillées : on inclut tout sauf la liste brute d'anomalies
        # (déjà dans metadata.anomalies_table) pour éviter la duplication.
        stats = {k: v for k, v in detection.items() if k != "anomalies"}

        return TaskResult(
            insights=[i.text for i in generated.insights],
            visualizations=viz_list,
            recommendations=[r.text for r in generated.recommendations],
            stats=stats,
            metadata=metadata,
            warnings=warnings,
            kg_payload=[],  # KG branché plus tard
        )

    # ─── Helpers privés ───────────────────────────────────────────────────

    def _build_anomalies_table(
        self,
        detection: dict[str, Any],
        is_multivariate: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Tableau structuré d'anomalies, prêt pour l'UI.

        Format univarié (IQR/Z-score) :
          [{"rank": 1, "date": "2025-03-10", "value": 78544.71,
            "deviation": 5000.0, "direction": "below"}, ...]

        Format multivarié (Isolation Forest) :
          [{"rank": 1, "date": "2025-03-10",
            "values": {"close_usd": 78000, "volume": 80000, "spread": 3000},
            "anomaly_score": 0.264}, ...]
        """
        anomalies = detection.get("anomalies", [])
        table: list[dict[str, Any]] = []
        for rank, a in enumerate(anomalies, start=1):
            entry: dict[str, Any] = {"rank": rank}
            if is_multivariate:
                entry["values"] = a.get("values", {})
                entry["anomaly_score"] = a.get("anomaly_score")
            else:
                entry["value"] = a.get("value")
                entry["deviation"] = a.get("deviation")
                entry["direction"] = a.get("direction")
                if "z_score" in a:
                    entry["z_score"] = a["z_score"]
            if "date" in a:
                entry["date"] = a["date"]
            table.append(entry)
        return table

    def _fetch_gdelt_enrichment(
        self,
        *,
        detection: dict[str, Any],
        df: pd.DataFrame,
        semantic_context: dict[str, Any] | None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """
        Récupère les articles GDELT publiés aux dates d'anomalies.

        Retourne ([], [warning]) si :
          - Pas d'anomalies détectées (rien à expliquer)
          - Pas de factory DB injectée (enrichissement désactivé)
          - Aucune date résolvable depuis les anomalies

        Sinon retourne la liste d'articles structurés + warnings éventuels.
        """
        # Pas d'anomalies → rien à enrichir.
        anomalies = detection.get("anomalies", [])
        if not anomalies:
            return [], []

        # Extraction des dates uniques des anomalies.
        dates: list[str] = []
        for a in anomalies:
            d = a.get("date")
            if d:
                # On garde uniquement YYYY-MM-DD (les timestamps ISO ont
                # souvent un T00:00:00 derrière)
                dates.append(str(d)[:10])
        dates = list(set(dates))

        if not dates:
            return [], [
                "anomaly_detection: anomalies sans date — "
                "enrichissement GDELT impossible"
            ]

        # Détection de l'entity_name pour filtrer les articles pertinents.
        # On utilise maintenant le nom de l'entité (Bitcoin, Ethereum...)
        # plutôt que crypto_id, car la colonne crypto_id dans la table
        # est souvent NULL (les keywords génériques n'ont pas de crypto
        # spécifique). Le filtrage par entity_name + tier cascade est
        # beaucoup plus robuste.
        entity_name = extract_entity_name_from_context(semantic_context)

        # Appel du module d'enrichissement.
        articles, warnings = fetch_gdelt_context(
            dates=dates,
            entity_name=entity_name,
            db_session_factory=self._db_session_factory,
        )

        if articles:
            logger.info(
                "anomaly_detection: %d article(s) GDELT récupéré(s) pour "
                "%d date(s) d'anomalie (entity='%s')",
                len(articles),
                len(dates),
                entity_name,
            )

        return articles, warnings

    def _generate_insights(
        self,
        *,
        detection: dict[str, Any],
        value_cols: list[str],
        date_col: str | None,
        n_rows: int,
        shape: str,
        semantic_context: dict[str, Any] | None,
        warnings_so_far: list[str],
        gdelt_articles: list[dict[str, Any]] | None = None,
        is_multivariate: bool = False,
    ) -> GeneratedInsights:
        """Appelle InsightGenerator avec un prompt 'anomaly_detection' dédié."""
        # Pour le fallback texte sans LLM, on prend la 1re colonne ou la liste.
        col_label = (
            ", ".join(value_cols) if is_multivariate else value_cols[0]
        )

        if self._insight_generator is None:
            from app.agents.analysis.llm.schemas import Insight

            warning = (
                "InsightGenerator non injecté dans AnomalyDetectionTask. "
                "Aucun insight NL généré."
            )
            logger.warning(warning)
            n = detection.get("n_anomalies", 0)
            text = (
                f"{n} anomalie(s) détectée(s) sur la colonne '{col_label}' "
                f"({n_rows} points analysés)."
                if n > 0
                else f"Aucune anomalie détectée sur la colonne '{col_label}'."
            )
            return GeneratedInsights(
                insights=[
                    Insight(
                        text=text,
                        confidence=EMPTY_RESULT_CONFIDENCE,
                        supporting_stats=["n_anomalies"],
                    )
                ],
                recommendations=[],
                overall_confidence=EMPTY_RESULT_CONFIDENCE,
                warnings=[warning],
                llm_metadata={"fallback": True, "no_generator": True},
                used_fallback=True,
            )

        # Stats passées au LLM = résumé compact + top anomalies.
        top_anomalies = detection.get("anomalies", [])[:10]
        stats_for_llm = {
            "n_points": detection.get("n_points", 0),
            "n_anomalies": detection.get("n_anomalies", 0),
            "anomaly_rate": detection.get("anomaly_rate", 0.0),
            "method": detection.get("auto_selected") or detection.get("method"),
            "thresholds": detection.get("thresholds"),
            "contamination": detection.get("contamination"),
            "top_anomalies": top_anomalies,
            "columns_analyzed": value_cols,
            "is_multivariate": is_multivariate,
        }

        return self._insight_generator.generate(
            task_name=self.task_name,
            stats=stats_for_llm,
            prompt_kwargs={
                "value_cols": value_cols,
                "date_col": date_col,
                "shape": shape,
                "detection": detection,
                "top_anomalies": top_anomalies,
                "semantic_hints": semantic_context,
                "warnings": list(warnings_so_far),
                "gdelt_articles": gdelt_articles or [],
                "is_multivariate": is_multivariate,
            },
        )

    def _build_empty_result(
        self,
        start_time: float,
        warnings: list[str],
        n_rows: int,
    ) -> TaskResult:
        """Résultat valide quand l'analyse n'est pas possible."""
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return TaskResult(
            insights=[],
            visualizations=[],
            recommendations=[],
            stats={},
            metadata={
                "task": self.task_name,
                "subtype": "empty",
                "confidence": EMPTY_RESULT_CONFIDENCE,
                "n_rows": n_rows,
                "method": None,
                "duration_ms": duration_ms,
                "n_anomalies": 0,
                "anomaly_rate": 0.0,
                "anomalies_table": [],
                "gdelt_articles": [],
                "gdelt_enrichment_used": False,
            },
            warnings=warnings,
            kg_payload=[],
        )