"""
app/agents/feedback/service.py

FeedbackService — Orchestration du pipeline Feedback Agent.

Enchaîne : collector → score_calculator → kg_updater.
Point d'entrée unique pour la route FastAPI.

Ne fait aucun appel LLM.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.agents.feedback.collector import FeedbackCollector
from app.agents.feedback.config import FeedbackConfig, feedback_config
from app.agents.feedback.kg_updater import KGUpdater
from app.agents.feedback.models import (
    FeedbackInput,
    FeedbackResult,
    ImplicitSignals,
)
from app.agents.feedback.score import ScoreCalculator

logger = logging.getLogger(__name__)


class FeedbackService:
    """Orchestre le pipeline complet du Feedback Agent.

    Parameters
    ----------
    neo4j_driver : Any
        Instance de Neo4jDriver (app/db/neo4j.py).
    config : FeedbackConfig | None
        Configuration des seuils et pondérations. Utilise la config
        globale par défaut.
    """

    def __init__(
        self,
        neo4j_driver: Any,
        config: FeedbackConfig | None = None,
    ) -> None:
        self._config = config or feedback_config
        self._collector = FeedbackCollector()
        self._score_calc = ScoreCalculator(config=self._config)
        self._kg_updater = KGUpdater(neo4j_driver=neo4j_driver)

    def process(
        self,
        feedback: FeedbackInput,
        implicit_signals: ImplicitSignals | None = None,
        execution_metadata: dict[str, Any] | None = None,
        expert_score: float | None = None,
    ) -> FeedbackResult:
        """Traite un feedback de bout en bout.

        Parameters
        ----------
        feedback : FeedbackInput
            Feedback explicite du frontend.
        implicit_signals : ImplicitSignals | None
            Signaux d'exécution. Si None, reconstruits depuis
            execution_metadata.
        execution_metadata : dict | None
            Métadonnées brutes du pipeline (temps, row_count, etc.).
        expert_score : float | None
            Score de validation expert (1–5). None par défaut
            (redistribution automatique).

        Returns
        -------
        FeedbackResult
        """
        feedback_id = str(uuid.uuid4())
        all_warnings: list[str] = []

        # ── Étape 1 : Collecte et validation ──────────────────
        validated_feedback, signals, collect_warnings = self._collector.collect(
            feedback=feedback,
            implicit_signals=implicit_signals,
            execution_metadata=execution_metadata,
        )
        all_warnings.extend(collect_warnings)

        # ── Étape 2 : Calcul du score composite ───────────────
        composite_score = self._score_calc.compute(
            feedback=validated_feedback,
            implicit=signals,
            expert_score=expert_score,
        )

        # ── Étape 3 : Persistence dans le KG ──────────────────
        kg_operations, kg_warnings = self._kg_updater.persist(
            feedback=validated_feedback,
            score=composite_score,
            feedback_id=feedback_id,
        )
        all_warnings.extend(kg_warnings)

        logger.info(
            "Feedback traité : id=%s, response_id=%s, score=%.2f (%s), "
            "%d opérations KG, %d warnings",
            feedback_id,
            feedback.response_id,
            composite_score.composite,
            composite_score.status.value,
            len(kg_operations),
            len(all_warnings),
        )

        return FeedbackResult(
            feedback_id=feedback_id,
            response_id=feedback.response_id,
            composite_score=composite_score,
            kg_updates=kg_operations,
            warnings=all_warnings,
        )
