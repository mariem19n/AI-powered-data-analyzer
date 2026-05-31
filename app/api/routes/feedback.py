"""
app/api/routes/feedback.py

MVP feedback route.

POST /api/feedback stores rating/comment feedback in PostgreSQL first, then
calls the existing Feedback Agent / Neo4j path on a best-effort basis.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.agents.feedback.models import (
    FeedbackInput,
    FeedbackType,
    ImplicitSignals,
    TargetType,
    ValidationStatus,
)
from app.agents.feedback.service import FeedbackService
from app.db.feedback import insert_user_feedback, update_user_feedback_validation
from app.db.neo4j import neo4j_driver

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["feedback"])


class FeedbackRequest(BaseModel):
    """Accepts both the legacy nested agent payload and the flat MVP payload."""

    # Legacy payload, still accepted for compatibility.
    feedback: FeedbackInput | None = None
    implicit_signals: ImplicitSignals | None = Field(default=None)
    execution_metadata: dict[str, Any] | None = Field(default=None)
    expert_score: float | None = Field(default=None, ge=1.0, le=5.0)

    # Flat MVP payload from the frontend.
    conversation_id: str | None = None
    message_id: str | None = None
    response_id: str | None = None
    session_id: str | None = None
    question: str | None = None
    intent: str | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    comment: str | None = None
    response_snapshot: dict[str, Any] | None = None
    feedback_type: str = "rating"

    @field_validator("comment")
    @classmethod
    def _clean_comment(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class FeedbackSubmitResponse(BaseModel):
    success: bool
    feedback_id: str
    message: str
    validation_status: str = "pending_review"
    credibility_score: float | None = None
    feedback_category: str | None = None
    needs_human_review: bool = False


def _build_service() -> FeedbackService:
    """Create an injectable FeedbackService for the current request."""
    return FeedbackService(neo4j_driver=neo4j_driver)


def _intent_to_text(intent: Any) -> str | None:
    if isinstance(intent, str):
        return intent
    if isinstance(intent, dict):
        primary = intent.get("primary")
        return str(primary) if primary else None
    return None


def _build_feedback_input(
    request: FeedbackRequest,
    *,
    stored_feedback_id: str,
) -> FeedbackInput:
    if request.feedback is not None:
        return request.feedback

    if request.rating is None:
        raise HTTPException(status_code=422, detail="rating is required")

    response_id = (
        request.response_id
        or request.message_id
        or request.session_id
        or stored_feedback_id
    )
    session_id = request.session_id or request.conversation_id or "anonymous"
    question = request.question or ""
    intent = request.intent or _intent_to_text(request.response_snapshot.get("intent") if request.response_snapshot else None)

    try:
        normalized_feedback_type = FeedbackType(request.feedback_type or "rating")
    except ValueError:
        normalized_feedback_type = FeedbackType.RATING

    return FeedbackInput(
        response_id=response_id,
        session_id=session_id,
        question=question,
        resolved_question=None,
        intent=intent,
        rating=request.rating,
        comment=request.comment,
        feedback_type=normalized_feedback_type,
        target_type=TargetType.ANSWER,
        target_id=None,
    )


@router.post("/feedback", response_model=FeedbackSubmitResponse)
async def submit_feedback(request: FeedbackRequest) -> FeedbackSubmitResponse:
    """Store MVP feedback and call the KG Feedback Agent best-effort."""
    from app.db.postgres import pg_pool

    rating = request.rating if request.rating is not None else request.feedback.rating if request.feedback else None
    if rating is None or not 1 <= int(rating) <= 5:
        raise HTTPException(status_code=422, detail="rating must be between 1 and 5")

    feedback_type = request.feedback.feedback_type.value if request.feedback else request.feedback_type or "rating"
    response_id = request.response_id or (request.feedback.response_id if request.feedback else None)
    session_id = request.session_id or (request.feedback.session_id if request.feedback else None)
    question = request.question or (request.feedback.question if request.feedback else None)
    intent = request.intent or (request.feedback.intent if request.feedback else None)
    comment = request.comment if request.comment is not None else request.feedback.comment if request.feedback else None

    try:
        stored = await insert_user_feedback(
            pg_pool.pool,
            conversation_id=request.conversation_id,
            message_id=request.message_id,
            response_id=response_id,
            session_id=session_id,
            question=question,
            intent=intent,
            rating=int(rating),
            comment=comment,
            feedback_type=feedback_type,
            response_snapshot=request.response_snapshot,
            implicit_signals=(
                request.implicit_signals.model_dump()
                if request.implicit_signals is not None
                else None
            ),
        )
    except Exception as exc:
        logger.error("PostgreSQL feedback insert failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Erreur lors de l'enregistrement du feedback.",
        ) from exc

    validation_status = ValidationStatus.PENDING_REVIEW.value
    credibility_score: float | None = None
    feedback_category: str | None = None
    needs_human_review = True

    try:
        feedback_input = _build_feedback_input(
            request,
            stored_feedback_id=stored["id"],
        )
        result = _build_service().process(
            feedback=feedback_input,
            implicit_signals=request.implicit_signals,
            execution_metadata=request.execution_metadata,
            expert_score=request.expert_score,
            response_snapshot=request.response_snapshot,
            feedback_id=stored["id"],
        )
        validation = result.validation_result
        if validation is not None:
            await update_user_feedback_validation(
                pg_pool.pool,
                feedback_id=stored["id"],
                feedback_category=validation.feedback_category.value,
                category_confidence=validation.category_confidence,
                credibility_score=validation.credibility_score,
                validation_status=validation.validation_status.value,
                validation_reason=validation.validation_reason,
                detected_signals=validation.detected_signals,
                judge_result=(
                    validation.judge_result.model_dump()
                    if validation.judge_result is not None
                    else None
                ),
                needs_human_review=validation.needs_human_review,
            )
            validation_status = validation.validation_status.value
            credibility_score = validation.credibility_score
            feedback_category = validation.feedback_category.value
            needs_human_review = validation.needs_human_review
    except Exception as exc:  # noqa: BLE001 - best-effort only
        logger.warning(
            "Feedback validation/KG best-effort call failed for feedback_id=%s: %s",
            stored["id"],
            exc,
            exc_info=True,
        )

    return FeedbackSubmitResponse(
        success=True,
        feedback_id=stored["id"],
        message="Feedback received and validated",
        validation_status=validation_status,
        credibility_score=credibility_score,
        feedback_category=feedback_category,
        needs_human_review=needs_human_review,
    )
