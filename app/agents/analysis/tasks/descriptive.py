"""
app/agents/analysis/tasks/descriptive.py
Task descriptive : statistiques de base + insights NL.

Première task de l'Analysis Agent. Elle assemble tous les composants
construits jusqu'ici :
  1. detect_dataframe_shape      → quel type de données on a
  2. summarize_numeric/timeseries/groupby  → stats brutes
  3. default_viz_for_shape       → viz adaptée à la forme
  4. InsightGenerator            → seul appel LLM
  5. construction du kg_payload  → écriture déléguée au runner

Aucune valeur métier hardcodée :
  - les noms de colonnes viennent de l'instruction OU sont auto-détectés
    par dtype via _select_columns()
  - le seuil de tendance, les quantiles, le min de points sont les défauts
    paramétrables des fonctions stats
  - aucun terme crypto/macro nulle part dans le code

L'ordre des sections du fichier suit le flux d'exécution de run() :
constantes → détection de colonnes → routage par shape → run().
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import pandas as pd

from app.agents.analysis.llm.insight_generator import (
    GeneratedInsights,
    InsightGenerator,
)
from app.agents.analysis.stats.descriptive import (
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
from app.agents.analysis.tasks.base import (
    AnalysisTask,
    TaskResult,
    register_task,
)
from app.agents.analysis.viz.templates import (
    default_viz_for_shape,
    get_default_viz_name_for_shape,
)

logger = logging.getLogger(__name__)


# ─── Constantes ────────────────────────────────────────────────────────────


# Limite au-delà de laquelle on tronque les groupes envoyés au LLM. 50 est un
# compromis : assez pour couvrir 99% des cas réels (10 cryptos, 11 séries macro,
# ~quelques régions ou catégories) sans surcharger le contexte du LLM.
DEFAULT_MAX_GROUPS_FOR_LLM = 50

# Confidence par défaut quand aucune info utile n'est extractible (DataFrame
# vide ou shape unknown). Volontairement basse pour que l'UI signale.
EMPTY_RESULT_CONFIDENCE = 0.2


# ─── Sélection des colonnes ───────────────────────────────────────────────


@dataclass
class _ColumnSelection:
    """Colonnes choisies pour l'analyse — soit par instruction, soit par défaut."""

    date_col: str | None = None
    value_col: str | None = None
    group_col: str | None = None
    extra_value_cols: list[str] | None = None  # multi-séries timeseries


def _select_columns(
    df: pd.DataFrame,
    shape_info: dict[str, Any],
    instruction: dict[str, Any],
) -> tuple[_ColumnSelection, list[str]]:
    """
    Choisit les colonnes à utiliser selon la shape détectée.

    Priorité :
    1. Si l'instruction explicite des colonnes (date_col, value_col, group_col),
       on les utilise telles quelles.
    2. Sinon on prend les premières colonnes du bon dtype détectées.

    Aucun nom de colonne n'est jamais hardcodé. Les noms viennent soit de
    l'instruction, soit de la détection par dtype.

    Returns:
        (selection, warnings)
    """
    warnings: list[str] = []
    sel = _ColumnSelection()

    datetime_cols = shape_info.get("datetime_cols", [])
    numeric_cols = shape_info.get("numeric_cols", [])
    categorical_cols = shape_info.get("categorical_cols", [])

    # ─ Date col ─
    requested_date = instruction.get("date_col")
    if isinstance(requested_date, str) and requested_date:
        if requested_date in df.columns:
            sel.date_col = requested_date
        else:
            warnings.append(
                f"date_col='{requested_date}' demandée mais absente du "
                f"DataFrame. Auto-détection."
            )
    if sel.date_col is None and datetime_cols:
        sel.date_col = datetime_cols[0]

    # ─ Value col ─
    requested_value = instruction.get("value_col")
    if isinstance(requested_value, str) and requested_value:
        if requested_value in df.columns:
            sel.value_col = requested_value
        else:
            warnings.append(
                f"value_col='{requested_value}' demandée mais absente du "
                f"DataFrame. Auto-détection."
            )
    if sel.value_col is None and numeric_cols:
        sel.value_col = numeric_cols[0]

    # ─ Extra value cols (multi-séries timeseries) ─
    requested_extra = instruction.get("extra_value_cols")
    if isinstance(requested_extra, list):
        valid = [c for c in requested_extra if c in df.columns and c != sel.value_col]
        invalid = [c for c in requested_extra if c not in df.columns]
        if invalid:
            warnings.append(
                f"extra_value_cols absente(s) du DataFrame : {invalid}"
            )
        sel.extra_value_cols = valid
    else:
        # Auto : toutes les autres colonnes numériques après value_col.
        sel.extra_value_cols = [
            c for c in numeric_cols if c != sel.value_col
        ]

    # ─ Group col ─
    requested_group = instruction.get("group_col")
    if isinstance(requested_group, str) and requested_group:
        if requested_group in df.columns:
            sel.group_col = requested_group
        else:
            warnings.append(
                f"group_col='{requested_group}' demandée mais absente du "
                f"DataFrame. Auto-détection."
            )
    if sel.group_col is None and categorical_cols:
        sel.group_col = categorical_cols[0]

    return sel, warnings


# ─── Calcul des stats par shape ───────────────────────────────────────────


def _compute_stats(
    df: pd.DataFrame,
    shape: str,
    selection: _ColumnSelection,
    instruction: dict[str, Any],
) -> tuple[dict[str, Any], str | None, list[str]]:
    """
    Calcule les stats appropriées selon la shape détectée.

    Returns:
        (stats, subtype, warnings) — subtype précise le sous-cas pour le LLM.
    """
    warnings: list[str] = []

    if shape == SHAPE_TIMESERIES:
        if selection.date_col is None or selection.value_col is None:
            warnings.append(
                "Shape timeseries détectée mais date_col ou value_col manquante."
            )
            return {}, None, warnings
        stats = summarize_timeseries(
            df=df,
            date_col=selection.date_col,
            value_col=selection.value_col,
        )
        subtype = (
            "timeseries_multi"
            if selection.extra_value_cols
            else "timeseries_single"
        )
        return stats, subtype, warnings

    if shape == SHAPE_GROUPBY:
        if selection.group_col is None or selection.value_col is None:
            warnings.append(
                "Shape groupby détectée mais group_col ou value_col manquante."
            )
            return {}, None, warnings
        max_groups = instruction.get("max_groups", DEFAULT_MAX_GROUPS_FOR_LLM)
        stats = summarize_groupby(
            df=df,
            group_col=selection.group_col,
            value_col=selection.value_col,
            max_groups=max_groups,
        )
        if stats.get("n_groups", 0) > stats.get("n_groups_returned", 0):
            warnings.append(
                f"Tronqué à {stats['n_groups_returned']}/{stats['n_groups']} "
                f"groupes pour le LLM (max_groups={max_groups})."
            )
        return stats, "groupby", warnings

    if shape == SHAPE_NUMERIC_ONLY:
        if selection.value_col is None:
            warnings.append("Shape numeric_only mais aucune colonne numérique sélectionnée.")
            return {}, None, warnings
        stats = summarize_numeric(df[selection.value_col])
        return stats, "distribution", warnings

    # SHAPE_EMPTY ou SHAPE_UNKNOWN : pas de stats calculables.
    return {}, None, warnings


# ─── Construction de la viz ───────────────────────────────────────────────


def _build_visualizations(
    df: pd.DataFrame,
    shape: str,
    selection: _ColumnSelection,
    instruction: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Construit la liste des visualisations pour le TaskResult.

    Délègue au registre `default_viz_for_shape`. Si aucune viz n'est
    enregistrée pour la shape, retourne une liste vide + warning.

    Returns:
        (visualizations, warnings)
    """
    warnings: list[str] = []
    viz_fn = default_viz_for_shape(shape)
    if viz_fn is None:
        warnings.append(
            f"Aucune viz par défaut enregistrée pour shape='{shape}'. "
            f"Pas de visualisation générée."
        )
        return [], warnings

    viz_name = get_default_viz_name_for_shape(shape) or "?"
    config = _build_viz_config(shape, selection, instruction)
    if config is None:
        warnings.append(
            f"Impossible de construire la config viz pour shape='{shape}' "
            f"(colonnes manquantes). Pas de visualisation générée."
        )
        return [], warnings

    try:
        viz_dict = viz_fn(df, config)
        return [viz_dict], warnings
    except Exception as e:  # noqa: BLE001 — viz error doit pas casser le pipeline
        logger.exception("Échec génération viz '%s'", viz_name)
        warnings.append(
            f"Échec génération viz '{viz_name}' : {type(e).__name__}: {e}"
        )
        return [], warnings


def _build_viz_config(
    shape: str,
    selection: _ColumnSelection,
    instruction: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Construit la config à passer à la fonction de viz selon la shape.

    Pour timeseries : x_col=date, y_cols=[value] + extra_value_cols.
    Pour groupby et numeric_only : non implémenté pour le MVP descriptive
    (nécessiterait bar_chart / histogram, hors scope ce ticket). On retourne
    None proprement pour que l'appelant ajoute un warning.

    Le titre et les labels sont passés depuis l'instruction si présents,
    sinon les fonctions de viz utilisent leurs défauts.
    """
    if shape == SHAPE_TIMESERIES:
        if selection.date_col is None or selection.value_col is None:
            return None
        y_cols = [selection.value_col]
        if selection.extra_value_cols:
            y_cols.extend(selection.extra_value_cols)
        config: dict[str, Any] = {
            "x_col": selection.date_col,
            "y_cols": y_cols,
        }
        # Titre et labels passés via instruction si fournis (jamais hardcodés).
        for key in ("title", "x_label", "y_label", "mode", "series_labels"):
            if key in instruction:
                config[key] = instruction[key]
        return config

    # Pour groupby et numeric_only : pas de viz dans le MVP descriptive.
    # Quand on ajoutera bar_chart et histogram, ce sera ici qu'on dispatchera.
    return None


# ─── Construction du kg_payload ───────────────────────────────────────────


def _build_kg_payload(
    generated: GeneratedInsights,
    task_name: str,
    shape: str,
    subtype: str | None,
    instruction: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Construit la liste d'entrées kg_payload à partir des insights générés.

    Format compatible avec kg_writer.py :
      - chaque Insight devient un nœud Insight dans le KG
      - relation DERIVED_FROM vers Question si question_id fourni dans l'instruction

    Aucun écriture ici — c'est le runner qui appelle KGWriter.write() ensuite.
    """
    payload: list[dict[str, Any]] = []
    question_id = instruction.get("question_id")

    for insight in generated.insights:
        properties: dict[str, Any] = {
            "text": insight.text,
            "confidence": insight.confidence,
            "supporting_stats": list(insight.supporting_stats),
            "task_name": task_name,
            "shape": shape,
        }
        if subtype:
            properties["subtype"] = subtype

        entry: dict[str, Any] = {
            "node_type": "Insight",
            "properties": properties,
        }

        if isinstance(question_id, str) and question_id:
            entry["relationships"] = [
                {
                    "type": "DERIVED_FROM",
                    "target_type": "Question",
                    "target_match": {"question_id": question_id},
                    "direction": "outgoing",
                }
            ]

        payload.append(entry)

    return payload


# ─── La task elle-même ─────────────────────────────────────────────────────


@register_task
class DescriptiveTask(AnalysisTask):
    """
    Task descriptive : routage par shape, stats brutes, insights NL.

    Sous-types pris en charge :
      - timeseries (mono ou multi-série)
      - groupby
      - numeric_only (distribution)
      - empty (DataFrame vide)
      - unknown (shape non reconnue)

    Pour les deux derniers cas, on renvoie un TaskResult valide avec un
    warning explicite — le pipeline ne casse jamais.
    """

    task_name = "descriptive"

    def __init__(self, insight_generator: InsightGenerator | None = None) -> None:
        """
        Args:
            insight_generator : injection optionnelle. Si None, le runner
                doit fournir le générateur par set_insight_generator() ou
                la task ne pourra produire que le fallback stat-based via
                un générateur instancié à la demande. Pour préserver le
                pattern de registry (qui instancie via cls()), on accepte
                None et on attend que set_insight_generator soit appelé
                avant run() — en pratique, c'est le runner qui le fait.
        """
        self._insight_generator: InsightGenerator | None = insight_generator

    def set_insight_generator(self, generator: InsightGenerator) -> None:
        """Permet au runner d'injecter le générateur après instanciation."""
        self._insight_generator = generator

    # ── Méthode principale ────────────────────────────────────────────────

    def run(
        self,
        df: pd.DataFrame,
        instruction: dict[str, Any],
        semantic_context: dict[str, Any] | None = None,
        **_unused: Any,  # ← absorber tout kwarg supplémentaire du runner
    ) -> TaskResult:
        """
        Exécute la task descriptive sur `df`.

        Args:
            df : DataFrame propre (issu du SQL Agent via le runner).
            instruction : dict d'instruction de l'Orchestrator. Champs lus :
                - date_col, value_col, group_col, extra_value_cols (optionnels)
                - title, x_label, y_label, mode, series_labels (passés à la viz)
                - max_groups (pour groupby)
                - question_id (pour la relation KG DERIVED_FROM)
            semantic_context : SemanticContext résolu en amont. Passé tel quel
                au LLM sous forme de hints (jamais introspecté dans la task).

        Returns:
            TaskResult complet et toujours valide.
        """
        start_time = time.perf_counter()
        warnings: list[str] = []

        # 1. Détection de shape.
        shape_info = detect_dataframe_shape(df)
        shape = shape_info["shape"]
        n_rows = shape_info["n_rows"]

        # 2. Cas dégénérés : empty ou unknown.
        if shape == SHAPE_EMPTY:
            warnings.append("DataFrame vide — aucune analyse possible.")
            return self._build_empty_result(
                task_subtype="empty",
                shape=shape,
                n_rows=n_rows,
                start_time=start_time,
                warnings=warnings,
            )

        if shape == SHAPE_UNKNOWN:
            warnings.append(
                f"Shape non reconnue (cols={shape_info['n_cols']}). "
                f"Aucune statistique standard applicable."
            )
            return self._build_empty_result(
                task_subtype="unknown",
                shape=shape,
                n_rows=n_rows,
                start_time=start_time,
                warnings=warnings,
            )

        # 3. Sélection des colonnes.
        selection, sel_warnings = _select_columns(df, shape_info, instruction)
        warnings.extend(sel_warnings)

        # 4. Calcul des stats.
        stats, subtype, stat_warnings = _compute_stats(
            df=df,
            shape=shape,
            selection=selection,
            instruction=instruction,
        )
        warnings.extend(stat_warnings)

        if not stats:
            return self._build_empty_result(
                task_subtype=subtype or shape,
                shape=shape,
                n_rows=n_rows,
                start_time=start_time,
                warnings=warnings,
            )

        # 5. Visualisation.
        visualizations, viz_warnings = _build_visualizations(
            df=df,
            shape=shape,
            selection=selection,
            instruction=instruction,
        )
        warnings.extend(viz_warnings)

        # 6. Génération des insights NL.
        generated = self._generate_insights(
            stats=stats,
            shape=shape,
            subtype=subtype,
            warnings_so_far=warnings,
            semantic_context=semantic_context,
        )
        warnings.extend(generated.warnings)

        # 7. Construction du kg_payload.
        kg_payload = _build_kg_payload(
            generated=generated,
            task_name=self.task_name,
            shape=shape,
            subtype=subtype,
            instruction=instruction,
        )

        # 8. Assemblage du TaskResult.
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        metadata: dict[str, Any] = {
            "task": self.task_name,
            "subtype": subtype,
            "confidence": generated.overall_confidence,
            "n_rows": n_rows,
            "method": None,  # descriptive n'a pas de "méthode" alternative
            "duration_ms": duration_ms,
            "fallback_used": generated.used_fallback,
        }
        if generated.llm_metadata:
            metadata["llm"] = generated.llm_metadata

        return TaskResult(
            insights=[i.text for i in generated.insights],
            visualizations=visualizations,
            recommendations=[r.text for r in generated.recommendations],
            stats=stats,
            metadata=metadata,
            warnings=warnings,
            kg_payload=kg_payload,
        )

    # ─── Helpers internes ────────────────────────────────────────────────

    def _generate_insights(
        self,
        *,
        stats: dict[str, Any],
        shape: str,
        subtype: str | None,
        warnings_so_far: list[str],
        semantic_context: dict[str, Any] | None,
    ) -> GeneratedInsights:
        """
        Appelle l'InsightGenerator avec le bon contexte.

        Si le générateur n'a pas été injecté (cas de test sans LLM),
        retourne un GeneratedInsights vide avec un warning — le fallback
        stat-based ne peut pas non plus être appelé sans générateur (parce
        que c'est lui qui le construit), donc on retourne minimal.
        """
        if self._insight_generator is None:
            from app.agents.analysis.llm.schemas import Insight

            warning = (
                "InsightGenerator non injecté dans DescriptiveTask. "
                "Aucun insight NL généré."
            )
            logger.warning(warning)
            return GeneratedInsights(
                insights=[
                    Insight(
                        text=(
                            f"{stats.get('n', 0)} observations analysées."
                            if stats.get("n")
                            else "Analyse statistique disponible."
                        ),
                        confidence=EMPTY_RESULT_CONFIDENCE,
                        supporting_stats=(["n"] if stats.get("n") else []),
                    )
                ],
                recommendations=[],
                overall_confidence=EMPTY_RESULT_CONFIDENCE,
                warnings=[warning],
                llm_metadata={"fallback": True, "no_generator": True},
                used_fallback=True,
            )

        return self._insight_generator.generate(
            task_name=self.task_name,
            stats=stats,
            prompt_kwargs={
                "shape": shape,
                "subtype": subtype,
                "warnings": list(warnings_so_far),  # propage aux warnings du LLM
                "semantic_hints": semantic_context,
            },
        )

    def _build_empty_result(
        self,
        *,
        task_subtype: str,
        shape: str,
        n_rows: int,
        start_time: float,
        warnings: list[str],
    ) -> TaskResult:
        """
        Construit un TaskResult valide pour les cas où aucune analyse n'est
        possible (DataFrame vide, shape unknown, colonnes manquantes).

        Confidence basse pour signaler à l'UI. Aucun insight, aucune viz,
        kg_payload vide.
        """
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return TaskResult(
            insights=[],
            visualizations=[],
            recommendations=[],
            stats={},
            metadata={
                "task": self.task_name,
                "subtype": task_subtype,
                "confidence": EMPTY_RESULT_CONFIDENCE,
                "n_rows": n_rows,
                "method": None,
                "duration_ms": duration_ms,
                "fallback_used": False,
            },
            warnings=warnings,
            kg_payload=[],
        )
