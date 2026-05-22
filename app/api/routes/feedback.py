"""
app/api/routes/feedback.py

Route FastAPI pour le Feedback Agent.

POST /api/feedback — soumettre un feedback utilisateur.

Cohérent avec la convention /api/* des endpoints frontend.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agents.feedback.models import (
    FeedbackInput,
    FeedbackResult,
    ImplicitSignals,
)
from app.agents.feedback.service import FeedbackService
from app.db.neo4j import neo4j_driver

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["feedback"])


# ── Request body enrichi (feedback + metadata optionnelle) ─────────────────


class FeedbackRequest(BaseModel):
    """Body de POST /api/feedback.

    Contient le feedback utilisateur + optionnellement les signaux
    implicites et le score expert.
    """

    feedback: FeedbackInput
    implicit_signals: ImplicitSignals | None = Field(
        default=None,
        description="Signaux d'exécution capturés automatiquement.",
    )
    execution_metadata: dict[str, Any] | None = Field(
        default=None,
        description="Métadonnées brutes du pipeline.",
    )
    expert_score: float | None = Field(
        default=None,
        ge=1.0,
        le=5.0,
        description="Score de validation expert (1–5).",
    )


# ── Singleton du service ───────────────────────────────────────────────────

_service: FeedbackService | None = None


def _get_service() -> FeedbackService:
    """Lazy init du FeedbackService."""
    global _service
    if _service is None:
        _service = FeedbackService(neo4j_driver=neo4j_driver)
    return _service


# ── Route ──────────────────────────────────────────────────────────────────


@router.post("/feedback", response_model=FeedbackResult)
def submit_feedback(request: FeedbackRequest) -> FeedbackResult:
    """Soumet un feedback utilisateur et retourne le score composite.

    Le pipeline :
      1. Collecte et valide le feedback
      2. Calcule le score composite pondéré (humain 62.5%, implicite 37.5%
         sans expert — redistribution automatique)
      3. Persiste dans le Knowledge Graph (nœud Feedback + liens + mises
         à jour de statut)

    Retourne le FeedbackResult avec le détail du score et les opérations KG.
    """
    try:
        service = _get_service()
        result = service.process(
            feedback=request.feedback,
            implicit_signals=request.implicit_signals,
            execution_metadata=request.execution_metadata,
            expert_score=request.expert_score,
        )
        return result

    except Exception as exc:
        logger.error(
            "Erreur traitement feedback : %s", exc, exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail=f"Erreur lors du traitement du feedback : {str(exc)}",
        ) from exc
