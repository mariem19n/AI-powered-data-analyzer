"""
app/agents/analysis/tasks/correlation.py

Task `correlation` : corrélation cross-table sur N séries temporelles.

Caractéristiques
----------------
- consumes_multiple_steps = True : reçoit le dict `upstream_results` brut
  avec N steps SQL (un par série).
- Merge les N DataFrames long format sur la colonne date pour produire
  un DataFrame wide.
- Calcule Pearson + Spearman sur `levels` ET `returns` (cf. stats/).
- Génère 1 ou 2 heatmaps (returns par défaut, levels en bonus si
  les deux blocs sont présents).
- Appelle l'InsightGenerator (1 seul appel LLM) pour produire les
  insights en langage naturel.
- Produit un kg_payload pour persistance en aval par le KGWriter.

Conventions respectées
----------------------
- Aucun nom de colonne / domaine hardcodé.
- Tous les seuils (returns vs levels par défaut, taille min, etc.) sont
  paramétrables via l'instruction ou hérités des constantes du module
  stats.
- Toute condition fragile → warning non-bloquant, jamais d'exception
  qui remonte.
- La task ne fait AUCUNE écriture (KG, DB, cache). Elle produit
  uniquement un TaskResult.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar, Literal

import pandas as pd

from app.agents.analysis.llm.insight_generator import InsightGenerator
from app.agents.analysis.stats.correlation import (
    DEFAULT_MIN_POINTS,
    compute_correlations,
    summarize_correlation,
)
from app.agents.analysis.tasks.base import (
    AnalysisTask,
    TaskResult,
    register_task,
)
from app.agents.analysis.viz.heatmap import build_correlation_heatmap

logger = logging.getLogger(__name__)


# ─── Constantes paramétrables ──────────────────────────────────────────────

# Nom de colonne date par défaut à chercher dans les DataFrames upstream.
# Surchargeable via instruction["date_col"].
_DEFAULT_DATE_COL: str = "date"

# Type de pré-traitement par défaut. Conforme à la décision du brief :
# "returns par défaut, levels en bonus dans metadata".
_DEFAULT_PRETREATMENT: Literal["returns", "levels", "both"] = "both"


# ─── Task ──────────────────────────────────────────────────────────────────


@register_task
class CorrelationTask(AnalysisTask):
    """
    Task `correlation` : corrélation Pearson + Spearman entre N séries
    temporelles produites par autant de steps SQL upstream.
    """

    task_name: ClassVar[str] = "correlation"
    consumes_multiple_steps: ClassVar[bool] = True

    def __init__(
        self, insight_generator: InsightGenerator | None = None
    ) -> None:
        self._insight_generator: InsightGenerator | None = insight_generator

    def set_insight_generator(self, generator: InsightGenerator) -> None:
        """Injecté par le runner au démarrage. Optionnel pour les tests."""
        self._insight_generator = generator

    # ── API publique : run() ─────────────────────────────────────────────

    def run(
        self,
        df: pd.DataFrame | None,
        instruction: dict[str, Any],
        semantic_context: dict[str, Any] | None = None,
        upstream_results: dict[str, Any] | None = None,
        **_unused: Any,
    ) -> TaskResult:
        warnings: list[str] = []
        upstream_results = upstream_results or {}
        semantic_context = semantic_context or {}

        # 1. Récupération des paramètres depuis l'instruction.
        date_col: str = str(
            instruction.get("date_col") or _DEFAULT_DATE_COL
        )
        pretreatment: str = str(
            instruction.get("pretreatment") or _DEFAULT_PRETREATMENT
        )
        if pretreatment not in ("returns", "levels", "both"):
            warnings.append(
                f"pretreatment='{pretreatment}' invalide. Fallback sur "
                f"'{_DEFAULT_PRETREATMENT}'."
            )
            pretreatment = _DEFAULT_PRETREATMENT
        min_points: int = int(
            instruction.get("min_points") or DEFAULT_MIN_POINTS
        )

        # 2. Identification des input_steps à utiliser.
        input_steps = instruction.get("input_steps") or list(
            upstream_results.keys()
        )
        if not input_steps:
            return self._build_empty_result(
                warnings=warnings
                + [
                    "Aucun step upstream fourni. "
                    "La task correlation a besoin d'au moins 2 séries."
                ]
            )
        if len(input_steps) < 2:
            return self._build_empty_result(
                warnings=warnings
                + [
                    f"Un seul step upstream fourni "
                    f"({input_steps}). La corrélation nécessite ≥ 2 séries."
                ]
            )

        # 3. Construction du DataFrame wide en mergeant chaque step.
        wide_df, merge_warnings, series_origin = _merge_steps_to_wide(
            upstream_results=upstream_results,
            input_steps=input_steps,
            date_col=date_col,
            semantic_context=semantic_context,
        )
        warnings.extend(merge_warnings)

        if wide_df is None or wide_df.shape[1] < 2:
            return self._build_empty_result(
                warnings=warnings
                + [
                    "Impossible de construire un DataFrame wide avec ≥ 2 "
                    "séries numériques après merge."
                ]
            )

        # 4. Calcul des corrélations (Pearson + Spearman, levels + returns).
        corr_result = compute_correlations(
            wide_df,
            date_col=None,  # déjà indexé par date après _merge_steps_to_wide
            series_cols=list(wide_df.columns),
            min_points=min_points,
            pretreatment=pretreatment,  # type: ignore[arg-type]
        )
        warnings.extend(corr_result.get("warnings", []))

        # 5. Construction des visualisations (heatmap returns en priorité,
        #    heatmap levels en bonus si disponible).
        visualizations = _build_visualizations(corr_result)

        # 6. Préparation des stats compactes pour le prompt LLM.
        stats_for_llm = summarize_correlation(corr_result)

        # 7. Génération des insights NL via LLM.
        generated = self._generate_insights(
            stats_for_llm=stats_for_llm,
            semantic_context=semantic_context,
            warnings_so_far=warnings,
        )

        insights_text: list[str] = []
        recommendations_text: list[str] = []
        overall_confidence: float | None = None
        methodology_note: str | None = None

        if generated is not None:
            insights_text = [i.text for i in generated.insights]
            recommendations_text = [r.text for r in generated.recommendations]
            overall_confidence = generated.overall_confidence
            methodology_note = getattr(generated, "methodology_note", None)
            warnings.extend(generated.warnings)
        else:
            warnings.append(
                "Génération d'insights indisponible : aucun texte produit."
            )

        # 8. Stats exposées dans le TaskResult (synthèse, pas la matrice
        #    complète — celle-ci va dans metadata si on veut la garder).
        result_stats: dict[str, Any] = {
            "n_series": corr_result.get("n_series", 0),
            "series_names": corr_result.get("series_names", []),
            "n_points_raw": corr_result.get("n_points_raw", 0),
        }
        for block_name in ("returns", "levels"):
            block = corr_result.get(block_name)
            if block:
                result_stats[block_name] = {
                    "n_points_used": block.get("n_points_used", 0),
                    "top_positive_pairs": block.get("top_positive_pairs", []),
                    "top_negative_pairs": block.get("top_negative_pairs", []),
                    "divergent_pairs": block.get("divergent_pairs", []),
                    "strong_pairs_count": block.get("strong_pairs_count", 0),
                    "thresholds": block.get("thresholds", {}),
                }

        # 9. Construction du kg_payload (Correlation nodes — un par paire
        #    forte). Suit le contrat de KGWriter (cf. anomaly_detection).
        kg_payload = _build_kg_payload(
            corr_result=corr_result,
            instruction=instruction,
            overall_confidence=overall_confidence,
        )

        # 10. Metadata détaillée. On inclut les matrices complètes ICI
        #     (et pas dans stats) pour ne pas surcharger les exports
        #     compacts.
        metadata: dict[str, Any] = {
            "task": self.task_name,
            "pretreatment": pretreatment,
            "date_col": date_col,
            "input_steps": input_steps,
            "series_origin": series_origin,
            "methodology_note": methodology_note,
        }
        # Matrices complètes pour debug / export, mais SEULEMENT les
        # dimensions raisonnables (sinon on bloate les logs).
        n_series = corr_result.get("n_series", 0)
        if n_series and n_series <= 12:
            metadata["full_matrices"] = {
                "levels": corr_result.get("levels") or {},
                "returns": corr_result.get("returns") or {},
            }

        return TaskResult(
            insights=insights_text,
            visualizations=visualizations,
            recommendations=recommendations_text,
            stats=result_stats,
            metadata=metadata,
            kg_payload=kg_payload,
            warnings=warnings,
        )

    # ── Helpers internes ─────────────────────────────────────────────────

    def _generate_insights(
        self,
        *,
        stats_for_llm: dict[str, Any],
        semantic_context: dict[str, Any] | None,
        warnings_so_far: list[str],
    ) -> Any:
        """
        Appelle l'InsightGenerator s'il est injecté. Retourne None sinon.
        Le runner aura déjà loggé un warning au démarrage si pas de
        generator — on n'en rajoute pas un ici.
        """
        if self._insight_generator is None:
            return None
        return self._insight_generator.generate(
            task_name=self.task_name,
            stats=stats_for_llm,
            prompt_kwargs={
                "semantic_hints": semantic_context,
                "warnings": list(warnings_so_far),
            },
        )

    def _build_empty_result(self, *, warnings: list[str]) -> TaskResult:
        """Résultat vide cohérent quand la task ne peut rien produire."""
        return TaskResult(
            insights=[],
            visualizations=[],
            recommendations=[],
            stats={},
            metadata={"task": self.task_name, "empty": True},
            kg_payload=[],
            warnings=warnings,
        )


# ─── Helpers du module ─────────────────────────────────────────────────────


def _merge_steps_to_wide(
    *,
    upstream_results: dict[str, Any],
    input_steps: list[str],
    date_col: str,
    semantic_context: dict[str, Any] | None,
) -> tuple[pd.DataFrame | None, list[str], dict[str, str]]:
    """
    Convertit N steps SQL (long format) en un DataFrame wide indexé par date.

    Chaque step doit produire un dict {records, columns, row_count, sql}.
    On extrait pour chaque step :
      - le DataFrame
      - la colonne "valeur" pertinente (auto-détection via le
        SemanticContext ou via dtype)
      - le nom logique de la série (entité résolue ou step_id en fallback)

    Returns:
        (wide_df | None, warnings, series_origin)
        où series_origin : {nom_série_finale -> step_id source} pour debug.
    """
    warnings: list[str] = []
    series_origin: dict[str, str] = {}
    per_step_series: list[pd.Series] = []

    for step_id in input_steps:
        step_out = upstream_results.get(step_id)
        if not step_out:
            warnings.append(
                f"Step '{step_id}' absent de upstream_results. Ignoré."
            )
            continue

        df = _step_to_dataframe(step_out)
        if df is None or df.empty:
            warnings.append(
                f"Step '{step_id}' : DataFrame vide ou non convertible. Ignoré."
            )
            continue

        if date_col not in df.columns:
            warnings.append(
                f"Step '{step_id}' : colonne date '{date_col}' absente. Ignoré."
            )
            continue

        # Choix de la colonne valeur.
        value_col, val_warn = _pick_value_column(df, date_col=date_col)
        warnings.extend(val_warn)
        if value_col is None:
            warnings.append(
                f"Step '{step_id}' : aucune colonne numérique exploitable. "
                "Ignoré."
            )
            continue

        # Choix du nom logique de la série.
        series_name = _build_series_name(
            step_id=step_id,
            df=df,
            value_col=value_col,
            semantic_context=semantic_context,
        )
        # Gérer les collisions de noms (au pire on suffixe par step_id).
        if series_name in series_origin:
            new_name = f"{series_name}__{step_id}"
            warnings.append(
                f"Nom de série en collision : '{series_name}' déjà utilisé "
                f"(step '{series_origin[series_name]}'). "
                f"Renommage en '{new_name}'."
            )
            series_name = new_name
        series_origin[series_name] = step_id

        # Conversion date + extraction de la série.
        try:
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        except Exception as e:  # noqa: BLE001
            warnings.append(
                f"Step '{step_id}' : conversion date échouée ({e}). Ignoré."
            )
            continue
        df = df.dropna(subset=[date_col])
        if df.empty:
            warnings.append(
                f"Step '{step_id}' : toutes les dates invalides. Ignoré."
            )
            continue

        s = df.set_index(date_col)[value_col]
        # Doublon de dates ? On garde la dernière obs.
        if s.index.duplicated().any():
            warnings.append(
                f"Step '{step_id}' : dates dupliquées, "
                "conservation de la dernière valeur par date."
            )
            s = s[~s.index.duplicated(keep="last")]
        s.name = series_name
        per_step_series.append(s)

    if len(per_step_series) < 2:
        warnings.append(
            f"Seulement {len(per_step_series)} série(s) exploitable(s) "
            "après merge. Corrélation impossible."
        )
        return None, warnings, series_origin

    # Merge sur l'index date par outer join puis sort.
    wide = pd.concat(per_step_series, axis=1, join="outer").sort_index()
    return wide, warnings, series_origin


def _step_to_dataframe(step_out: dict[str, Any]) -> pd.DataFrame | None:
    """
    Convertit la sortie d'un step SQL Agent en DataFrame.

    Le format conventionnel :
      {"records": [{...}, ...], "columns": [...], "row_count": int, "sql": str}
    """
    if not isinstance(step_out, dict):
        return None
    records = step_out.get("records")
    if not records:
        return None
    try:
        return pd.DataFrame.from_records(records)
    except Exception as e:  # noqa: BLE001
        logger.warning("Conversion DataFrame impossible: %s", e)
        return None


def _pick_value_column(
    df: pd.DataFrame, *, date_col: str
) -> tuple[str | None, list[str]]:
    """
    Choisit la colonne "valeur" d'un DataFrame long.

    Heuristiques (dans l'ordre) :
      1. Si exactement une colonne numérique hors `date_col` : on la prend.
      2. S'il y en a plusieurs : on cherche des noms conventionnels
         (`close_usd`, `value`, `price`, `level`). Le premier qui match gagne.
      3. À défaut : on prend la première colonne numérique non-date.
    """
    warnings: list[str] = []
    numeric_cols = [
        c
        for c in df.columns
        if c != date_col and pd.api.types.is_numeric_dtype(df[c])
    ]
    if not numeric_cols:
        return None, warnings

    if len(numeric_cols) == 1:
        return numeric_cols[0], warnings

    # Plusieurs candidates : noms conventionnels finance/macro.
    # Cette liste n'est PAS un vocabulaire métier hardcodé — c'est une
    # heuristique de fallback documentée. Aucune logique de domaine ne
    # dépend d'elle.
    conventional_names = [
        "close_usd",
        "close",
        "value",
        "price",
        "level",
        "amount",
        "rate",
    ]
    for name in conventional_names:
        if name in numeric_cols:
            warnings.append(
                f"Plusieurs colonnes numériques disponibles {numeric_cols} ; "
                f"sélection de '{name}' par convention."
            )
            return name, warnings

    chosen = numeric_cols[0]
    warnings.append(
        f"Plusieurs colonnes numériques disponibles {numeric_cols} ; "
        f"sélection de '{chosen}' (première trouvée). "
        "Préciser explicitement via instruction['value_col'] si nécessaire."
    )
    return chosen, warnings


def _build_series_name(
    *,
    step_id: str,
    df: pd.DataFrame,
    value_col: str,
    semantic_context: dict[str, Any] | None,
) -> str:
    """
    Construit un nom de série lisible.

    Ordre de priorité :
      1. Entité résolue dans le SemanticContext qui matche le step.
      2. Une colonne `symbol` / `series_id` / `entity` présente et constante.
      3. Combinaison `value_col + step_id`.
    """
    # Plan 2 : colonnes d'identité courantes dans les fact tables.
    for id_col in ("symbol", "series_id", "entity", "crypto_id", "ticker"):
        if id_col in df.columns:
            uniq = df[id_col].dropna().unique()
            if len(uniq) == 1:
                return str(uniq[0])

    # Plan 3 : fallback.
    return f"{value_col}__{step_id}"


def _build_visualizations(
    corr_result: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Construit les heatmaps. Returns en priorité ; levels en bonus si présent.
    """
    out: list[dict[str, Any]] = []
    series_names = corr_result.get("series_names", []) or []
    n_points_raw = corr_result.get("n_points_raw", 0)
    if not series_names:
        return out

    for block_name, label in (
        ("returns", "Returns (variations)"),
        ("levels", "Levels (niveaux bruts)"),
    ):
        block = corr_result.get(block_name)
        if not block or not block.get("matrix_pearson"):
            continue

        # Reconstruction de la matrice 2D dans l'ordre des labels.
        matrix_dict = block["matrix_pearson"]
        try:
            matrix_2d = [
                [matrix_dict[r].get(c) for c in series_names]
                for r in series_names
            ]
        except KeyError:
            logger.warning(
                "Heatmap : incohérence labels/matrix pour bloc '%s'. Skip.",
                block_name,
            )
            continue

        fig = build_correlation_heatmap(
            {
                "labels": series_names,
                "matrix": matrix_2d,
                "title": f"Matrice de corrélation — {label}",
                "subtitle": (
                    f"n={block.get('n_points_used', 0)} points, "
                    f"Pearson"
                ),
                "method": "pearson",
            }
        )
        out.append(fig)

    return out


def _build_kg_payload(
    *,
    corr_result: dict[str, Any],
    instruction: dict[str, Any],
    overall_confidence: float | None,
) -> list[dict[str, Any]]:
    """
    Produit la liste de nodes à persister dans le KG.

    Un node `Correlation` par paire forte (intensity == "strong") sur le bloc
    `returns` (priorité) ou `levels` si returns absent. Pas de duplication.
    Confidence par node = overall_confidence du LLM si fourni, sinon 0.5.
    """
    payload: list[dict[str, Any]] = []
    question_id = instruction.get("question_id")
    block_used = "returns" if corr_result.get("returns") else "levels"
    block = corr_result.get(block_used) or {}
    pairs = block.get("all_pairs", []) or []

    conf = overall_confidence if overall_confidence is not None else 0.5

    for p in pairs:
        if p.get("intensity") != "strong":
            continue
        if p.get("pearson") is None:
            continue

        node = {
            "node_type": "Correlation",
            "properties": {
                "series_a": p["a"],
                "series_b": p["b"],
                "pearson": p["pearson"],
                "spearman": p["spearman"],
                "block": block_used,
                "n_points_used": block.get("n_points_used", 0),
                "confidence": float(conf),
            },
            "relationships": [],
        }
        if question_id:
            node["relationships"].append(
                {
                    "type": "DERIVED_FROM",
                    "target_type": "Question",
                    "target_match": {"question_id": question_id},
                    "direction": "outgoing",
                }
            )
        payload.append(node)

    return payload
