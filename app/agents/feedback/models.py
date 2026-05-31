"""Pydantic schemas for the Feedback Agent pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class FeedbackType(str, Enum):
    """Type of feedback submitted by the user."""

    RATING = "rating"
    POSITIVE = "positive"
    NEGATIVE = "negative"
    COMMENT = "comment"
    CORRECTION_SQL = "correction_sql"
    CORRECTION_INSIGHT = "correction_insight"
    FALSE_POSITIVE = "false_positive"
    USELESS = "useless"


class TargetType(str, Enum):
    """Object type targeted by the feedback."""

    ANSWER = "answer"
    INSIGHT = "insight"
    SQL = "sql"
    ANOMALY = "anomaly"
    FORECAST = "forecast"
    CORRELATION = "correlation"


class FeedbackStatus(str, Enum):
    """Status derived from the legacy composite score."""

    REINFORCED = "reinforced"
    DEPRECATED = "deprecated"
    NEUTRAL = "neutral"


class FeedbackCategory(str, Enum):
    """Deterministic category assigned to feedback text."""

    POSITIVE_FEEDBACK = "positive_feedback"
    OPINION_OR_EMOTION = "opinion_or_emotion"
    TECHNICAL_ERROR = "technical_error"
    SEMANTIC_ERROR = "semantic_error"
    DATA_ERROR = "data_error"
    VISUALIZATION_ERROR = "visualization_error"
    SOURCE_ERROR = "source_error"
    FORMATTING_OR_TONE = "formatting_or_tone"
    VAGUE_NEGATIVE = "vague_negative"
    CORRECTION_SUGGESTION = "correction_suggestion"
    UNCLASSIFIED = "unclassified"


class ValidationStatus(str, Enum):
    """Context-aware validation status for feedback credibility."""

    ACCEPTED = "accepted"
    PENDING_REVIEW = "pending_review"
    LOW_CONFIDENCE = "low_confidence"
    REJECTED = "rejected"


class FeedbackInput(BaseModel):
    """Payload normalized for the Feedback Agent."""

    response_id: str = Field(description="Unique ID of the evaluated response.")
    session_id: str = Field(description="Conversation/session ID.")
    question: str = Field(description="Original user question.")
    resolved_question: str | None = Field(default=None)
    intent: str | None = Field(default=None)
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None)
    feedback_type: FeedbackType = Field(default=FeedbackType.RATING)
    target_type: TargetType = Field(default=TargetType.ANSWER)
    target_id: str | None = Field(default=None)
    corrected_sql: str | None = Field(default=None)
    corrected_text: str | None = Field(default=None)

    @field_validator("rating")
    @classmethod
    def rating_in_range(cls, value: int) -> int:
        """Validate the explicit user rating."""
        if not 1 <= value <= 5:
            raise ValueError("rating must be between 1 and 5")
        return value


class ImplicitSignals(BaseModel):
    """Implicit behavioral and execution signals captured around feedback."""

    execution_success: bool = Field(default=True)
    response_time_seconds: float | None = Field(default=None)
    null_rate: float | None = Field(default=None)
    llm_confidence: float | None = Field(default=None)
    row_count: int | None = Field(default=None)
    warnings_count: int = Field(default=0)
    dwell_time_ms: int | None = Field(default=None)
    response_char_count: int | None = Field(default=None)
    copied_response: bool = Field(default=False)
    copy_zone: str | None = Field(default=None)
    opened_sources: bool = Field(default=False)
    opened_details: bool = Field(default=False)
    expanded_visualization: bool = Field(default=False)
    exported_report: bool = Field(default=False)
    reran_question: bool = Field(default=False)
    follow_up_question: str | None = Field(default=None)
    reformulation_similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    reformulation_detected: bool | None = Field(default=None)
    warnings_visible: bool = Field(default=False)
    response_had_visualization: bool = Field(default=False)


class ClassificationResult(BaseModel):
    """Output of the deterministic feedback text classifier."""

    feedback_category: FeedbackCategory = Field(default=FeedbackCategory.UNCLASSIFIED)
    category_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    extracted_claims: list[str] = Field(default_factory=list)
    is_specific: bool = Field(default=False)
    mentions_verifiable_element: bool = Field(default=False)


class JudgeResult(BaseModel):
    """Structured output of the optional LLM Judge."""

    judge_verdict: str = Field(
        description="supports_user_feedback | contradicts_user_feedback | inconclusive"
    )
    judge_confidence: float = Field(ge=0.0, le=1.0)
    judge_reason: str
    error_type: str


class ValidationResult(BaseModel):
    """Context-aware validation result for a feedback item."""

    validation_status: ValidationStatus
    credibility_score: float = Field(ge=0.0, le=1.0)
    validation_reason: str
    feedback_category: FeedbackCategory
    category_confidence: float = Field(ge=0.0, le=1.0)
    detected_signals: dict[str, Any] = Field(default_factory=dict)
    needs_human_review: bool = Field(default=False)
    judge_result: JudgeResult | None = Field(default=None)
    warnings: list[str] = Field(default_factory=list)


class CompositeScore(BaseModel):
    """Legacy weighted score result on a 1-5 scale."""

    score_human: float | None = Field(default=None)
    score_implicit: float | None = Field(default=None)
    score_expert: float | None = Field(default=None)
    weight_human: float
    weight_implicit: float
    weight_expert: float
    composite: float
    status: FeedbackStatus


class FeedbackResult(BaseModel):
    """Result returned by the Feedback Agent service."""

    feedback_id: str
    response_id: str
    composite_score: CompositeScore
    kg_updates: list[str] = Field(default_factory=list)
    validation_result: ValidationResult | None = Field(default=None)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
