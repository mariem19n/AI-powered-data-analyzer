from __future__ import annotations

from app.agents.feedback.models import (
    FeedbackCategory,
    FeedbackInput,
    ImplicitSignals,
    JudgeResult,
    ValidationStatus,
)
from app.agents.feedback.text_classifier import FeedbackTextClassifier
from app.agents.feedback.validator import FeedbackValidator


def _feedback(
    *,
    rating: int = 1,
    comment: str | None = None,
    feedback_type: str = "negative",
) -> FeedbackInput:
    return FeedbackInput(
        response_id="response-1",
        session_id="session-1",
        question="Compare BTC and ETH volume",
        intent="comparison",
        rating=rating,
        comment=comment,
        feedback_type=feedback_type,
    )


def _validate(
    *,
    feedback: FeedbackInput,
    signals: ImplicitSignals,
    snapshot: dict | None = None,
    judge=None,
):
    validator = FeedbackValidator(judge=judge)
    return validator.validate(
        feedback=feedback,
        implicit_signals=signals,
        response_snapshot=snapshot or {"insights": ["ETH has higher volume."]},
    )


def test_fast_dislike_without_reading_is_low_confidence():
    result = _validate(
        feedback=_feedback(comment=None),
        signals=ImplicitSignals(dwell_time_ms=800, response_char_count=1500),
    )

    assert result.validation_status == ValidationStatus.LOW_CONFIDENCE
    assert result.credibility_score < 0.35


def test_specific_correction_with_verifiable_claim_is_credible():
    result = _validate(
        feedback=_feedback(comment="Le SQL utilise ETH au lieu de BTC"),
        signals=ImplicitSignals(opened_details=True, response_char_count=500, dwell_time_ms=10000),
    )

    assert result.validation_status in {
        ValidationStatus.PENDING_REVIEW,
        ValidationStatus.ACCEPTED,
    }
    assert result.credibility_score > 0.70
    assert result.feedback_category == FeedbackCategory.TECHNICAL_ERROR


def test_dislike_after_copy_without_comment_is_reduced():
    result = _validate(
        feedback=_feedback(comment=None),
        signals=ImplicitSignals(
            copied_response=True,
            copy_zone=None,
            dwell_time_ms=10000,
            response_char_count=500,
        ),
    )

    assert result.validation_status == ValidationStatus.LOW_CONFIDENCE
    assert result.credibility_score < 0.45


def test_copy_sql_with_sql_complaint_skips_copy_penalty():
    result = _validate(
        feedback=_feedback(comment="SQL query is wrong"),
        signals=ImplicitSignals(
            copied_response=True,
            copy_zone="sql",
            dwell_time_ms=10000,
            response_char_count=500,
        ),
    )

    assert result.feedback_category == FeedbackCategory.TECHNICAL_ERROR
    assert result.credibility_score > 0.70


def test_vague_negative_without_comment_is_pending_review():
    result = _validate(
        feedback=_feedback(comment=None),
        signals=ImplicitSignals(dwell_time_ms=10000, response_char_count=500),
    )

    assert result.validation_status == ValidationStatus.PENDING_REVIEW
    assert 0.35 <= result.credibility_score <= 0.45


def test_positive_quick_thumbs_up_is_reduced():
    result = _validate(
        feedback=_feedback(rating=5, comment=None, feedback_type="positive"),
        signals=ImplicitSignals(dwell_time_ms=500, response_char_count=2000),
    )

    assert result.credibility_score < 0.35
    assert result.feedback_category == FeedbackCategory.POSITIVE_FEEDBACK


def test_engaged_positive_feedback_is_accepted_without_human_review():
    result = _validate(
        feedback=_feedback(rating=5, comment=None, feedback_type="positive"),
        signals=ImplicitSignals(
            opened_sources=True,
            opened_details=True,
            expanded_visualization=True,
            exported_report=True,
            dwell_time_ms=61921,
            response_char_count=353,
        ),
    )

    assert result.validation_status == ValidationStatus.ACCEPTED
    assert result.credibility_score >= 0.60
    assert result.needs_human_review is False
    assert "Positive feedback with strong engagement" in result.validation_reason


def test_source_complaint_on_external_response_with_missing_sources_is_boosted():
    result = _validate(
        feedback=_feedback(comment="wrong sources"),
        signals=ImplicitSignals(dwell_time_ms=10000, response_char_count=500),
        snapshot={"response_mode": "external", "sources": [], "insights": ["Result"]},
    )

    assert result.feedback_category == FeedbackCategory.SOURCE_ERROR
    assert result.credibility_score > 0.85


class _Judge:
    def __init__(self, verdict: str) -> None:
        self.verdict = verdict

    def judge(self, *, feedback, response_snapshot):
        return (
            JudgeResult(
                judge_verdict=self.verdict,
                judge_confidence=0.9,
                judge_reason="mocked",
                error_type="factual_error",
            ),
            [],
        )


def test_llm_judge_supports_user_boosts_score():
    result = _validate(
        feedback=_feedback(comment="Le SQL utilise ETH au lieu de BTC"),
        signals=ImplicitSignals(dwell_time_ms=10000, response_char_count=500),
        judge=_Judge("supports_user_feedback"),
    )

    assert result.judge_result is not None
    assert result.credibility_score >= 0.85
    assert result.validation_status == ValidationStatus.ACCEPTED


def test_llm_judge_contradicts_user_never_drops_below_pending_review():
    result = _validate(
        feedback=_feedback(comment="Le SQL utilise ETH au lieu de BTC"),
        signals=ImplicitSignals(dwell_time_ms=1000, response_char_count=2000),
        judge=_Judge("contradicts_user_feedback"),
    )

    assert result.judge_result is not None
    assert result.validation_status == ValidationStatus.PENDING_REVIEW


def test_text_classifier_supports_french_and_english_visualization_errors():
    classifier = FeedbackTextClassifier()

    assert (
        classifier.classify("Le graphique est faux").feedback_category
        == FeedbackCategory.VISUALIZATION_ERROR
    )
    assert (
        classifier.classify("wrong chart").feedback_category
        == FeedbackCategory.VISUALIZATION_ERROR
    )
