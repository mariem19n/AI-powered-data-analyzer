"""
app/agents/analysis/runner.py
AnalysisAgent : orchestrateur de la couche tasks.

Responsabilités du runner (et SEULEMENT celles-ci) :
  1. Recevoir une instruction de l'Orchestrator (via PlanExecutor)
  2. Convertir les upstream_results (records SQL) en DataFrame pandas
  3. Récupérer la bonne task via le registry (get_task)
  4. Injecter l'InsightGenerator dans la task si elle l'accepte
  5. Appeler task.run(df, instruction, semantic_context)
  6. Écrire le kg_payload du TaskResult dans Neo4j via KGWriter
  7. Retourner le résultat sérialisé pour l'Aggregator

Le runner ne contient AUCUNE logique d'analyse. Tout ce qui est analyse vit
dans tasks/, stats/, viz/, llm/. Le runner est un pur dispatcher.

Tolérance aux pannes :
  - Pas de upstream_results → warning + DataFrame vide passé à la task
  - Conversion records → DataFrame échoue → warning + DataFrame vide
  - Task lève une exception → result d'erreur avec warning, pas de crash
  - KG indisponible → warning ajouté au TaskResult, l'analyse est quand même retournée
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import pandas as pd

from app.agents.analysis.kg_writer import KGWriter, Neo4jDriverLike
from app.agents.analysis.llm.insight_generator import InsightGenerator
from app.agents.analysis.tasks.base import (
    AnalysisTask,
    TaskResult,
    get_task,
    list_registered_tasks,
)

logger = logging.getLogger(__name__)


# ─── Constantes ────────────────────────────────────────────────────────────


# Variable d'env dédiée pour le modèle LLM de l'Analysis Agent. Si absente,
# on retombe sur le client partagé (qui lit LLM_MODEL).
ANALYSIS_LLM_MODEL_ENV = "ANALYSIS_LLM_MODEL"


# ─── Conversion records → DataFrame ────────────────────────────────────────


def _records_to_dataframe(records: list[dict[str, Any]] | None) -> pd.DataFrame:
    """
    Convertit la liste de records (format SQL Agent : list[dict[col -> value]])
    en DataFrame pandas.

    Tente une conversion automatique des colonnes ressemblant à des dates :
    si toutes les valeurs non-nulles d'une colonne object sont parsables en
    datetime, la colonne est convertie. Évite que des dates ISO arrivent en
    str et empêchent detect_dataframe_shape de classer en timeseries.

    Returns:
        DataFrame (potentiellement vide) — ne lève jamais.
    """
    if not records:
        return pd.DataFrame()

    try:
        df = pd.DataFrame(records)
    except Exception as e:  # noqa: BLE001 — input externe, doit pas casser
        logger.exception("Échec conversion records → DataFrame: %s", e)
        return pd.DataFrame()

    # Tentative de conversion datetime sur colonnes object/string.
    # Pandas peut inférer soit 'object' soit 'string' pour des chaînes —
    # on couvre les deux cas. On exclut explicitement les colonnes déjà
    # datetime ou numériques pour éviter du travail inutile.
    for col in df.columns:
        if (
            pd.api.types.is_datetime64_any_dtype(df[col])
            or pd.api.types.is_numeric_dtype(df[col])
            or pd.api.types.is_bool_dtype(df[col])
        ):
            continue
        non_null = df[col].dropna()
        if non_null.empty:
            continue
        try:
            converted = pd.to_datetime(non_null, errors="raise", utc=False)
            # Réinjecte en gardant les NaN aux bonnes positions.
            df[col] = pd.to_datetime(df[col], errors="coerce")
            logger.debug(
                "Colonne '%s' auto-convertie en datetime (%d non-null)",
                col,
                len(non_null),
            )
            _ = converted  # variable utilisée pour le check
        except (ValueError, TypeError):
            # Pas une date → on laisse en l'état.
            pass

    return df


# ─── Sélection des upstream results ───────────────────────────────────────


def _extract_dataframe_from_upstream(
    upstream_results: dict[str, Any],
    instruction: dict[str, Any],
) -> tuple[pd.DataFrame, list[str]]:
    """
    Extrait un DataFrame depuis les upstream_results selon l'instruction.

    L'instruction peut spécifier :
      - input_steps: list[str] — quels steps prendre en upstream
      - cross_table: bool — si True, joint plusieurs upstream sur une colonne
                            (réservé à anomaly/correlation, ignoré ici)

    Pour la task descriptive, on prend le premier input_step. Si plusieurs
    sont fournis, on log un info et on ignore les autres — descriptive ne
    s'applique qu'à un seul DataFrame par construction.

    Returns:
        (df, warnings)
    """
    warnings: list[str] = []

    if not upstream_results:
        warnings.append(
            "Aucun upstream_result fourni à l'AnalysisAgent. DataFrame vide."
        )
        return pd.DataFrame(), warnings

    input_steps = instruction.get("input_steps")

    # Cas 1 : input_steps explicite.
    if isinstance(input_steps, list) and input_steps:
        if len(input_steps) > 1:
            logger.info(
                "AnalysisAgent: %d input_steps fournis, prise du premier ('%s'). "
                "(cross-table sera géré par les tasks anomaly/correlation plus tard)",
                len(input_steps),
                input_steps[0],
            )
        chosen = input_steps[0]
        upstream = upstream_results.get(chosen)
        if upstream is None:
            warnings.append(
                f"input_step '{chosen}' demandé mais absent de upstream_results "
                f"(disponibles : {list(upstream_results.keys())})."
            )
            return pd.DataFrame(), warnings
    else:
        # Cas 2 : pas d'input_steps explicite → on prend le premier upstream.
        chosen, upstream = next(iter(upstream_results.items()))
        warnings.append(
            f"input_steps non fourni dans l'instruction. "
            f"Utilisation du premier upstream '{chosen}'."
        )

    # Extraction des records selon le format du SQL Agent.
    if isinstance(upstream, dict) and "records" in upstream:
        records = upstream.get("records")
    elif isinstance(upstream, list):
        records = upstream
    else:
        warnings.append(
            f"Format upstream '{chosen}' non reconnu (type={type(upstream).__name__}). "
            f"Attendu : dict avec 'records' ou list."
        )
        return pd.DataFrame(), warnings

    df = _records_to_dataframe(records)
    if df.empty and records:
        warnings.append(
            f"Conversion records → DataFrame a produit un DataFrame vide "
            f"depuis {len(records)} record(s)."
        )
    return df, warnings


# ─── Protocol pour le client LLM (pour build_default uniquement) ──────────


class _LLMClientLike(Protocol):
    """Sous-ensemble de l'API LLMClient utilisé par le runner pour la factory."""

    model: str

    def chat_json_schema(
        self, system: str, user: str, schema: Any, **kwargs: Any
    ) -> Any: ...


# ─── Réponse du runner ─────────────────────────────────────────────────────


@dataclass
class AnalysisResponse:
    """
    Résultat retourné par AnalysisAgent.run() au PlanExecutor.

    Format compatible avec ce que l'Aggregator attend : dict des champs du
    TaskResult + métadonnées d'exécution du runner (task name réelle, durée,
    erreurs éventuelles).
    """

    task: str
    insights: list[str] = field(default_factory=list)
    visualizations: list[dict[str, Any]] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {
            "task": self.task,
            "insights": self.insights,
            "visualizations": self.visualizations,
            "recommendations": self.recommendations,
            "stats": self.stats,
            "metadata": self.metadata,
            "warnings": self.warnings,
        }
        if self.error is not None:
            out["error"] = self.error
        return out


# ─── L'agent ───────────────────────────────────────────────────────────────


@dataclass
class AnalysisAgent:
    """
    Runner de l'Analysis Agent.

    Args:
        insight_generator : composant LLM injecté. Toutes les tasks le reçoivent
            via set_insight_generator() avant run() si elles l'acceptent.
        kg_writer : writer Neo4j injecté. Si None, l'écriture KG est skip
            silencieusement (avec un warning).

    Pour la prod, utiliser AnalysisAgent.build_default(...).
    Pour les tests, instancier directement avec mocks.
    """

    insight_generator: InsightGenerator | None = None
    kg_writer: KGWriter | None = None
    # Factory injecté par l'app pour ouvrir une connexion read-only à
    # PostgreSQL (utilisé par les tasks d'enrichissement, ex. GDELT pour
    # les anomalies). Si None, l'enrichissement est skip silencieusement.
    db_session_factory: Any = None

    # ── API publique ──────────────────────────────────────────────────────

    def run(
        self,
        instruction: dict[str, Any],
        upstream_results: dict[str, Any] | None = None,
        semantic_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Exécute la task désignée par instruction["task"].

        Cette signature respecte le Protocol AgentRunner attendu par
        l'orchestrator/executor.py : (instruction, upstream_results, semantic_context) -> Any.

        Args:
            instruction : dict produit par l'Orchestrator. Doit contenir
                au minimum "task". Champs optionnels selon la task :
                input_steps, cross_table, date_col, value_col, group_col,
                question_id, title, etc.
                Le `semantic_context` peut soit être passé en keyword
                (priorité), soit imbriqué dans `instruction["semantic_context"]`
                (legacy / fallback).
            upstream_results : dict[step_id -> sortie agent amont]. Format
                de sortie du SQL Agent : {records, columns, row_count, sql}.
            semantic_context : contexte résolu par le Semantic Layer.
                Si None, on retombe sur instruction["semantic_context"].

        Returns:
            dict (forme AnalysisResponse.to_dict()) — toujours valide,
            jamais d'exception levée. Stocké dans StepResult.data.
        """
        start_time = time.perf_counter()
        warnings: list[str] = []

        # 0. Résolution du semantic_context.
        #    Priorité au keyword (passé par le PlanExecutor),
        #    fallback sur instruction["semantic_context"] (legacy).
        if semantic_context is None and isinstance(instruction, dict):
            semantic_context = instruction.get("semantic_context")

        # 1. Validation de l'instruction.
        task_name = instruction.get("task") if isinstance(instruction, dict) else None
        if not isinstance(task_name, str) or not task_name:
            return self._build_error_response(
                task=str(task_name) if task_name else "<unknown>",
                error_msg="instruction['task'] manquant ou invalide",
                start_time=start_time,
            ).to_dict()

        # 2. Récupération de la task via registry.
        try:
            task = get_task(task_name)
        except KeyError as e:
            return self._build_error_response(
                task=task_name,
                error_msg=(
                    f"Task '{task_name}' inconnue. "
                    f"Tasks enregistrées : {list_registered_tasks()}. "
                    f"Détail : {e}"
                ),
                start_time=start_time,
            ).to_dict()

        # 3. Injection de l'InsightGenerator si la task le supporte.
        if hasattr(task, "set_insight_generator"):
            if self.insight_generator is None:
                warnings.append(
                    "InsightGenerator non disponible côté runner — la task "
                    "tournera en mode dégradé sans LLM."
                )
            else:
                task.set_insight_generator(self.insight_generator)

        # 3.5 Injection du db_session_factory si la task le supporte
        # (utilisé pour l'enrichissement GDELT côté anomaly_detection).
        if hasattr(task, "set_db_session_factory") and self.db_session_factory is not None:
            task.set_db_session_factory(self.db_session_factory)

        # 4. Préparation des inputs selon le contrat de la task.
        # Lookup du flag sur la CLASSE (pas l'instance) parce que les
        # tasks sont enregistrées comme classes dans le registry, instanciées
        # à la volée. `task` ici est une instance.
        task_class = task.__class__
        consumes_multi = bool(
            getattr(task_class, "consumes_multiple_steps", False)
        )

        df: pd.DataFrame | None
        if consumes_multi:
            # La task gère elle-même l'extraction depuis upstream_results.
            # On ne lit aucun step ici. La task doit déclarer `input_steps` dans son instruction et naviguer le dict `upstream_results`.
            df = None
            logger.debug(
                "Task '%s' déclare consumes_multiple_steps=True ; "
                "transmission brute de upstream_results (steps=%s).",
                task_name,
                sorted((upstream_results or {}).keys()),
            )
        else:
            # Comportement classique : on extrait un seul DataFrame du premier step utilisable.
            df, upstream_warnings = _extract_dataframe_from_upstream(
                upstream_results=upstream_results or {},
                instruction=instruction,
            )
            warnings.extend(upstream_warnings)

        # 5. Exécution de la task.
        try:
            task_result = task.run(
                df=df,
                instruction=instruction,
                semantic_context=semantic_context,
                upstream_results=upstream_results or {},
            )
        except Exception as e:  # noqa: BLE001 — protection ultime
            logger.exception("Échec dans task.run() pour task=%s", task_name)
            return self._build_error_response(
                task=task_name,
                error_msg=f"Échec de la task : {type(e).__name__}: {e}",
                start_time=start_time,
                accumulated_warnings=warnings,
            ).to_dict()

        # 6. Écriture du kg_payload.
        kg_warnings = self._write_kg_payload(task_result)
        warnings.extend(kg_warnings)

        # 7. Assemblage de la réponse finale.
        warnings.extend(task_result.warnings)

        response = AnalysisResponse(
            task=task_name,
            insights=task_result.insights,
            visualizations=task_result.visualizations,
            recommendations=task_result.recommendations,
            stats=task_result.stats,
            metadata={
                **task_result.metadata,
                # On surcouche la duration_ms du runner (englobe tout)
                "runner_duration_ms": int(
                    (time.perf_counter() - start_time) * 1000
                ),
            },
            warnings=warnings,
        )
        return response.to_dict()

    # ── Helpers internes ──────────────────────────────────────────────────

    def _write_kg_payload(self, task_result: TaskResult) -> list[str]:
        """
        Écrit le kg_payload de la task dans Neo4j si le writer est disponible.

        Returns:
            warnings produits par l'écriture (vide si pas d'écriture).
        """
        if not task_result.kg_payload:
            return []

        if self.kg_writer is None:
            return [
                "KGWriter non configuré côté runner — kg_payload non écrit "
                f"({len(task_result.kg_payload)} entrée(s) ignorée(s))."
            ]

        try:
            write_result = self.kg_writer.write(task_result.kg_payload)
            logger.info(
                "KG write : %d écrit, %d échec(s)",
                write_result.written_count,
                write_result.failed_count,
            )
            return list(write_result.warnings)
        except Exception as e:  # noqa: BLE001 — KG ne doit jamais crasher l'agent
            logger.exception("Échec inattendu KGWriter.write()")
            return [
                f"KGWriter.write() a levé une exception : {type(e).__name__}: {e}"
            ]

    def _build_error_response(
        self,
        *,
        task: str,
        error_msg: str,
        start_time: float,
        accumulated_warnings: list[str] | None = None,
    ) -> AnalysisResponse:
        """Construit une AnalysisResponse d'erreur (toujours valide structurellement)."""
        runner_duration_ms = int((time.perf_counter() - start_time) * 1000)
        return AnalysisResponse(
            task=task,
            insights=[],
            visualizations=[],
            recommendations=[],
            stats={},
            metadata={
                "task": task,
                "confidence": 0.0,
                "n_rows": 0,
                "runner_duration_ms": runner_duration_ms,
            },
            warnings=accumulated_warnings or [],
            error=error_msg,
        )

    # ── Factory de production ─────────────────────────────────────────────

    @classmethod
    def build_default(
        cls,
        *,
        llm_client: _LLMClientLike | None = None,
        neo4j_driver: Neo4jDriverLike | None = None,
        db_session_factory: Any = None,
    ) -> AnalysisAgent:
        """
        Construit un AnalysisAgent prêt pour la prod.

        Args:
            llm_client : client LLM partagé. Si None, instancie un LLMClient
                avec le modèle ANALYSIS_LLM_MODEL si défini, sinon le client
                singleton partagé (LLM_MODEL).
            neo4j_driver : driver Neo4j partagé. Si None, l'écriture KG est
                désactivée (warning à chaque task ayant un kg_payload).
            db_session_factory : callable qui retourne une connexion
                PostgreSQL (read-only, context manager). Utilisée par
                l'AnalysisAgent pour l'enrichissement GDELT des anomalies.
                Si None, l'enrichissement GDELT est silencieusement skip.

        Returns:
            AnalysisAgent configuré.
        """
        # Résolution du client LLM.
        client = llm_client
        if client is None:
            from app.llm import LLMClient, get_llm_client

            analysis_model = os.getenv(ANALYSIS_LLM_MODEL_ENV)
            if analysis_model:
                logger.info(
                    "AnalysisAgent: client LLM dédié (model=%s)",
                    analysis_model,
                )
                client = LLMClient(model=analysis_model)
            else:
                logger.info(
                    "AnalysisAgent: %s non défini, utilisation du client partagé",
                    ANALYSIS_LLM_MODEL_ENV,
                )
                client = get_llm_client()

        insight_generator = InsightGenerator(client=client)

        # Résolution du KG writer.
        kg_writer: KGWriter | None
        if neo4j_driver is not None:
            kg_writer = KGWriter(driver=neo4j_driver)
        else:
            kg_writer = None
            logger.warning(
                "AnalysisAgent: aucun driver Neo4j fourni à build_default(). "
                "L'écriture KG sera désactivée."
            )

        return cls(
            insight_generator=insight_generator,
            kg_writer=kg_writer,
            db_session_factory=db_session_factory,
        )