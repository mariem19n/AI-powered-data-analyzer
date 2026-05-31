"""Feedback Agent service orchestration.

Pipeline: collector -> validator -> score_calculator -> kg_updater.
PostgreSQL persistence is handled by the API layer; this service only
normalizes, validates, scores, and optionally propagates accepted feedback to
Neo4j.
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
    ValidationStatus,
)
from app.agents.feedback.score import ScoreCalculator
from app.agents.feedback.validator import FeedbackValidator

logger = logging.getLogger(__name__)


class FeedbackService:
    """Orchestrate feedback validation and accepted-only KG propagation."""

    def __init__(
        self,
        neo4j_driver: Any,
        config: FeedbackConfig | None = None,
        validator: FeedbackValidator | None = None,
    ) -> None:
        self._config = config or feedback_config
        self._collector = FeedbackCollector()
        self._validator = validator or FeedbackValidator(config=self._config)
        self._score_calc = ScoreCalculator(config=self._config)
        self._kg_updater = KGUpdater(neo4j_driver=neo4j_driver)

    def process(
        self,
        feedback: FeedbackInput,
        implicit_signals: ImplicitSignals | None = None,
        execution_metadata: dict[str, Any] | None = None,
        expert_score: float | None = None,
        response_snapshot: dict[str, Any] | None = None,
        feedback_id: str | None = None,
    ) -> FeedbackResult:
        """Run collector, validator, scorer and accepted-only KG propagation."""
        feedback_id = feedback_id or str(uuid.uuid4())
        all_warnings: list[str] = []

        validated_feedback, signals, collect_warnings = self._collector.collect(
            feedback=feedback,
            implicit_signals=implicit_signals,
            execution_metadata=execution_metadata,
        )
        all_warnings.extend(collect_warnings)

        validation_result = self._validator.validate(
            feedback=validated_feedback,
            implicit_signals=signals,
            response_snapshot=response_snapshot,
        )
        all_warnings.extend(validation_result.warnings)

        composite_score = self._score_calc.compute(
            feedback=validated_feedback,
            implicit=signals,
            expert_score=expert_score,
        )

        kg_operations: list[str] = []
        if validation_result.validation_status == ValidationStatus.ACCEPTED:
            kg_operations, kg_warnings = self._kg_updater.persist(
                feedback=validated_feedback,
                score=composite_score,
                feedback_id=feedback_id,
            )
            all_warnings.extend(kg_warnings)
        else:
            logger.info(
                "Feedback %s kept in PostgreSQL only: validation_status=%s",
                feedback_id,
                validation_result.validation_status.value,
            )

        logger.info(
            "Feedback processed: id=%s response_id=%s composite=%.2f "
            "validation=%s kg_ops=%d warnings=%d",
            feedback_id,
            feedback.response_id,
            composite_score.composite,
            validation_result.validation_status.value,
            len(kg_operations),
            len(all_warnings),
        )

        return FeedbackResult(
            feedback_id=feedback_id,
            response_id=feedback.response_id,
            composite_score=composite_score,
            kg_updates=kg_operations,
            validation_result=validation_result,
            warnings=all_warnings,
        )
