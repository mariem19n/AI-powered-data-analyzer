"""
app/agents/feedback/collector.py

FeedbackCollector — Validation et normalisation du feedback entrant.

Responsabilités :
  - Valider la cohérence du FeedbackInput (type vs champs fournis)
  - Enrichir avec les ImplicitSignals si disponibles
  - Produire un objet normalisé prêt pour le ScoreCalculator

Ne fait aucun appel LLM, aucune écriture KG, aucun accès Redis.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.feedback.models import (
    FeedbackInput,
    FeedbackType,
    ImplicitSignals,
)

logger = logging.getLogger(__name__)


class FeedbackCollector:
    """Collecte et normalise le feedback utilisateur + signaux implicites."""

    def collect(
        self,
        feedback: FeedbackInput,
        implicit_signals: ImplicitSignals | None = None,
        execution_metadata: dict[str, Any] | None = None,
    ) -> tuple[FeedbackInput, ImplicitSignals, list[str]]:
        """Valide le feedback et retourne un tuple normalisé.

        Parameters
        ----------
        feedback : FeedbackInput
            Feedback explicite de l'utilisateur.
        implicit_signals : ImplicitSignals | None
            Signaux d'exécution capturés automatiquement.
            Si None, on tente de les reconstruire depuis execution_metadata.
        execution_metadata : dict | None
            Métadonnées brutes du pipeline (temps, row_count, warnings…).

        Returns
        -------
        tuple[FeedbackInput, ImplicitSignals, list[str]]
            (feedback validé, signaux implicites, warnings)
        """
        warnings: list[str] = []

        # ── Validation de cohérence type/champs ────────────────
        warnings.extend(self._validate_consistency(feedback))

        # ── Construction des signaux implicites ────────────────
        if implicit_signals is None:
            implicit_signals = self._build_implicit_from_metadata(
                execution_metadata or {}, warnings
            )

        logger.info(
            "Feedback collecté : response_id=%s, rating=%d, type=%s, "
            "target=%s, implicit_available=%s",
            feedback.response_id,
            feedback.rating,
            feedback.feedback_type.value,
            feedback.target_type.value,
            implicit_signals is not None,
        )

        return feedback, implicit_signals, warnings

    # ── Validation interne ─────────────────────────────────────

    @staticmethod
    def _validate_consistency(feedback: FeedbackInput) -> list[str]:
        """Vérifie la cohérence entre feedback_type et les champs fournis."""
        warnings: list[str] = []

        if (
            feedback.feedback_type == FeedbackType.CORRECTION_SQL
            and not feedback.corrected_sql
        ):
            warnings.append(
                "feedback_type=correction_sql mais corrected_sql est vide. "
                "Le feedback sera traité comme un rating simple."
            )

        if (
            feedback.feedback_type == FeedbackType.CORRECTION_INSIGHT
            and not feedback.corrected_text
        ):
            warnings.append(
                "feedback_type=correction_insight mais corrected_text est vide. "
                "Le feedback sera traité comme un rating simple."
            )

        if (
            feedback.feedback_type == FeedbackType.FALSE_POSITIVE
            and feedback.target_id is None
        ):
            warnings.append(
                "feedback_type=false_positive sans target_id. "
                "Impossible d'identifier l'anomalie cible."
            )

        return warnings

    # ── Construction implicite depuis metadata ─────────────────

    @staticmethod
    def _build_implicit_from_metadata(
        metadata: dict[str, Any],
        warnings: list[str],
    ) -> ImplicitSignals:
        """Reconstruit les ImplicitSignals depuis les metadata du pipeline.

        Clés attendues dans metadata (toutes optionnelles) :
          - execution_success: bool
          - response_time_seconds: float
          - null_rate: float
          - llm_confidence: float
          - row_count: int
          - warnings_count: int
        """
        try:
            return ImplicitSignals(
                execution_success=metadata.get("execution_success", True),
                response_time_seconds=metadata.get("response_time_seconds"),
                null_rate=metadata.get("null_rate"),
                llm_confidence=metadata.get("llm_confidence"),
                row_count=metadata.get("row_count"),
                warnings_count=metadata.get("warnings_count", 0),
            )
        except Exception as exc:
            warnings.append(
                f"Échec construction ImplicitSignals depuis metadata : {exc}. "
                "Utilisation des valeurs par défaut."
            )
            return ImplicitSignals()
