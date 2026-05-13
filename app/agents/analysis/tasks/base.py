"""
app/agents/analysis/tasks/base.py
Contrat partagé pour toutes les tasks de l'Analysis Agent.

Conception clé :
- `AnalysisTask` est une interface abstraite. Chaque task concrète (descriptive,
  anomaly, correlation, ...) hérite et implémente `run()`.
- `TaskResult` est le format unique de sortie de toutes les tasks. C'est ce que
  l'AnalysisAgent retourne à l'Orchestrator (via Aggregator).
- `TASK_REGISTRY` est un registre auto-peuplé via le décorateur `@register_task`.
  Le runner choisit la task à partir d'`instruction["task"]` sans qu'aucun
  dispatcher hardcodé n'ait à connaître la liste des tasks. Ajouter une nouvelle
  task = créer un fichier + décorer la classe. Rien d'autre à modifier.

Aucune valeur métier n'est codée ici : pas de noms de colonnes, pas de seuils,
pas de méthodes statistiques. Cette couche est purement structurelle.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

import pandas as pd

logger = logging.getLogger(__name__)


# ─── TaskResult ────────────────────────────────────────────────────────────


@dataclass
class TaskResult:
    """
    Sortie unique de toute AnalysisTask.

    L'AnalysisAgent.run() agrège ce résultat dans le format attendu par
    l'Aggregator de l'Orchestrator :
        {
            "insights": [...],
            "visualizations": [...],
            "recommendations": [...],
            "stats": {...},        # bruts, pour le KG et le debug
            "metadata": {...},     # task, subtype, confidence, ...
            "warnings": [...],     # remontées non-bloquantes
        }

    Champs :
    - insights : phrases en NL générées par le LLM
    - visualizations : list de dicts Plotly JSON-serializable
    - recommendations : actionnables NL générés par le LLM
    - stats : résultats statistiques bruts (calculés sans LLM)
    - metadata : info structurée sur l'exécution. Voir METADATA_CONVENTION
                 ci-dessous pour les clés réservées.
    - warnings : remontées non-bloquantes destinées à l'utilisateur final.
                 Exemples : "DataFrame vide", "Pas assez de points pour
                 détecter des anomalies (n=3, requis=10)", "Colonne 'volume'
                 introuvable", "Jointure cross-table a réduit le DataFrame
                 de 365 à 120 lignes". Le LLM peut les utiliser pour moduler
                 la confiance de son insight ; l'UI peut les afficher.
    - kg_payload : objets à écrire dans Neo4j (Insight, Anomaly, ...). Le runner
                   délègue l'écriture au kg_writer après réception du TaskResult.
                   Garde la task pure (zero side-effect KG dans la task elle-même).
    """

    insights: list[str] = field(default_factory=list)
    visualizations: list[dict[str, Any]] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    kg_payload: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Sérialisation pour l'Aggregator. kg_payload est exclu (interne)."""
        return {
            "insights": self.insights,
            "visualizations": self.visualizations,
            "recommendations": self.recommendations,
            "stats": self.stats,
            "metadata": self.metadata,
            "warnings": self.warnings,
        }


# ─── Convention metadata ───────────────────────────────────────────────────


# Clés réservées dans `TaskResult.metadata`. Chaque task est libre d'ajouter
# ses clés spécifiques (ex: "iqr_multiplier", "lag_days") À CÔTÉ de celles-ci,
# mais ne doit pas redéfinir une clé réservée avec un sens différent.
#
# Format : { clé: (type_attendu, requis, description) }
# Cette table est utilisée par _validate_metadata() — pas de hardcoding ailleurs.
METADATA_CONVENTION: dict[str, tuple[type | tuple[type, ...], bool, str]] = {
    "task": (str, True, "Nom de la task (rempli par le runner si manquant)"),
    "subtype": ((str, type(None)), False, "Variant interne ('timeseries', ...)"),
    "confidence": ((int, float), True, "Confiance globale, float ∈ [0, 1]"),
    "n_rows": (int, True, "Nombre de lignes du DataFrame analysé"),
    "method": ((str, type(None)), False, "Méthode stat utilisée ('iqr', ...)"),
    "duration_ms": ((int, float, type(None)), False, "Durée d'exécution"),
}


def _validate_metadata(
    metadata: dict[str, Any],
    task_name: str,
) -> list[str]:
    """
    Vérifie qu'une `metadata` respecte la convention partagée.

    Retourne la liste des problèmes détectés (vide si tout est OK).
    Le runner décide quoi en faire (logger un warning, ajouter dans
    `TaskResult.warnings`, etc.). Ne lève jamais d'exception : une
    convention violée n'est pas un crash, c'est un signal.

    Vérifications :
    - clés obligatoires présentes
    - types respectés pour les clés réservées présentes
    - confidence dans [0, 1]
    - n_rows >= 0
    """
    issues: list[str] = []

    for key, (expected_type, required, _desc) in METADATA_CONVENTION.items():
        if key not in metadata:
            if required:
                issues.append(
                    f"[{task_name}] metadata manquante : '{key}' est requise"
                )
            continue
        value = metadata[key]
        if not isinstance(value, expected_type):
            issues.append(
                f"[{task_name}] metadata['{key}'] type invalide : "
                f"attendu {expected_type}, reçu {type(value).__name__}"
            )

    confidence = metadata.get("confidence")
    if isinstance(confidence, (int, float)) and not (0.0 <= float(confidence) <= 1.0):
        issues.append(
            f"[{task_name}] metadata['confidence']={confidence} hors [0, 1]"
        )

    n_rows = metadata.get("n_rows")
    if isinstance(n_rows, int) and n_rows < 0:
        issues.append(f"[{task_name}] metadata['n_rows']={n_rows} négatif")

    return issues


# ─── AnalysisTask ABC ──────────────────────────────────────────────────────


class AnalysisTask(ABC):
    """
    Interface abstraite que toute task d'analyse doit implémenter.

    Une task concrète :
    1. Déclare un attribut de classe `task_name` (str) — la clé utilisée par
       l'instruction de l'Orchestrator pour la sélectionner.
    2. Implémente `run(df, instruction, semantic_context)` et retourne un
       TaskResult.

    Les tasks concrètes ne font jamais d'I/O directes (pas de DB, pas de Redis,
    pas d'écriture Neo4j). Elles produisent un TaskResult ; le runner s'occupe
    des side-effects (écriture KG via kg_writer).

    L'appel LLM, lui, est encapsulé dans `app/agents/analysis/llm/` et appelé
    par la task quand elle a besoin de générer du NL.
    """

    # Doit être surchargé par chaque sous-classe concrète.
    task_name: ClassVar[str] = ""
    consumes_multiple_steps: ClassVar[bool] = False

    @abstractmethod
    def run(
        self,
        df: pd.DataFrame,
        instruction: dict[str, Any],
        semantic_context: dict[str, Any] | None = None,
        upstream_results: dict[str, Any] | None = None,
    ) -> TaskResult:
        """
        Exécute la task.

        Contrat selon `consumes_multiple_steps` :
          - False (défaut) : `df` est un DataFrame propre extrait par le
            runner du PREMIER step upstream. `upstream_results` doit être
            ignoré (le runner peut le passer ou non).
          - True           : `df` vaut None. `upstream_results` contient
            {step_id -> sortie SQL Agent}. La task est responsable de
            l'extraction et du merge des DataFrames qui l'intéressent.

        Les tasks existantes (descriptive, anomaly_detection) continuent à
        utiliser leur signature de run actuelle ; le param `upstream_results`
        leur est passé en keyword par le runner mais ignoré côté task.
        """
        raise NotImplementedError


# ─── Registry auto-peuplé ──────────────────────────────────────────────────


# Registre global : task_name -> classe AnalysisTask.
# Peuplé via le décorateur @register_task. Aucune liste hardcodée nulle part.
_TASK_REGISTRY: dict[str, type[AnalysisTask]] = {}


def register_task(cls: type[AnalysisTask]) -> type[AnalysisTask]:
    """
    Décorateur de classe : enregistre une AnalysisTask dans le registre global.

    Usage :
        @register_task
        class DescriptiveTask(AnalysisTask):
            task_name = "descriptive"
            def run(self, df, instruction, semantic_context=None):
                ...

    Validation :
    - La classe doit hériter d'AnalysisTask.
    - `task_name` doit être non vide.
    - Pas de collision avec un task_name déjà enregistré.
    """
    if not issubclass(cls, AnalysisTask):
        raise TypeError(
            f"register_task: {cls.__name__} doit hériter d'AnalysisTask"
        )
    name = getattr(cls, "task_name", "")
    if not name:
        raise ValueError(
            f"register_task: {cls.__name__}.task_name est vide. "
            f"Définis un nom de task non vide."
        )
    if name in _TASK_REGISTRY:
        existing = _TASK_REGISTRY[name].__name__
        raise ValueError(
            f"register_task: collision sur task_name='{name}'. "
            f"Déjà enregistré par {existing}, tentative depuis {cls.__name__}."
        )
    _TASK_REGISTRY[name] = cls
    logger.debug("Registered analysis task: %s -> %s", name, cls.__name__)
    return cls


def get_task(task_name: str) -> AnalysisTask:
    """
    Récupère une instance de la task associée à `task_name`.

    Lève KeyError avec un message listant les tasks disponibles si non trouvée.
    """
    cls = _TASK_REGISTRY.get(task_name)
    if cls is None:
        available = sorted(_TASK_REGISTRY.keys())
        raise KeyError(
            f"AnalysisTask inconnue : '{task_name}'. "
            f"Tasks enregistrées : {available}"
        )
    return cls()


def list_registered_tasks() -> list[str]:
    """Retourne la liste triée des task_name enregistrés (debug / health check)."""
    return sorted(_TASK_REGISTRY.keys())


def _clear_registry_for_tests() -> None:
    """
    Vide le registre. À n'utiliser que dans les tests pour garantir l'isolation.
    Ne pas appeler en production.
    """
    _TASK_REGISTRY.clear()