"""Context-aware feedback validation and credibility scoring."""

from __future__ import annotations

import logging
from difflib import SequenceMatcher
from typing import Any, Protocol

from app.agents.feedback.config import FeedbackConfig, feedback_config
from app.agents.feedback.models import (
    ClassificationResult,
    FeedbackCategory,
    FeedbackInput,
    FeedbackType,
    ImplicitSignals,
    JudgeResult,
    ValidationResult,
    ValidationStatus,
)
from app.agents.feedback.text_classifier import FeedbackTextClassifier

logger = logging.getLogger(__name__)


class _JudgeLike(Protocol):
    def judge(
        self,
        *,
        feedback: FeedbackInput,
        response_snapshot: dict[str, Any],
    ) -> tuple[JudgeResult | None, list[str]]:
        ...


_JUDGE_CATEGORIES = {
    FeedbackCategory.TECHNICAL_ERROR,
    FeedbackCategory.SEMANTIC_ERROR,
    FeedbackCategory.DATA_ERROR,
    FeedbackCategory.SOURCE_ERROR,
    FeedbackCategory.VISUALIZATION_ERROR,
    FeedbackCategory.CORRECTION_SUGGESTION,
}


class FeedbackValidator:
    """Validate feedback credibility without deciding by rating alone."""

    def __init__(
        self,
        *,
        classifier: FeedbackTextClassifier | None = None,
        judge: _JudgeLike | None = None,
        config: FeedbackConfig | None = None,
    ) -> None:
        self._config = config or feedback_config
        self._classifier = classifier or FeedbackTextClassifier(config=self._config)
        self._judge = judge

    def validate(
        self,
        *,
        feedback: FeedbackInput,
        implicit_signals: ImplicitSignals,
        response_snapshot: dict[str, Any] | None = None,
    ) -> ValidationResult:
        """Compute a credibility score and validation status for feedback."""
        snapshot = response_snapshot or {}
        warnings: list[str] = []
        signals = self._prepare_signals(feedback, implicit_signals)
        classification = self._classify(feedback)
        is_positive = _is_positive_feedback(feedback)
        credibility = (
            self._positive_score(classification, feedback, signals, snapshot)
            if is_positive
            else self._base_score(classification, feedback, signals, snapshot)
        )

        judge_result: JudgeResult | None = None
        judge_floor_pending = False
        if self._should_run_judge(
            feedback=feedback,
            classification=classification,
            response_snapshot=snapshot,
            credibility_score=credibility,
        ):
            if self._judge is None:
                warnings.append("LLM Judge skipped: no judge configured")
            else:
                judge_result, judge_warnings = self._judge.judge(
                    feedback=feedback,
                    response_snapshot=snapshot,
                )
                warnings.extend(judge_warnings)
                if judge_result is not None:
                    credibility, judge_floor_pending = self._apply_judge(
                        credibility, judge_result
                    )

        status = self._status_for(credibility, is_positive=is_positive)
        if judge_floor_pending and status in {
            ValidationStatus.LOW_CONFIDENCE,
            ValidationStatus.REJECTED,
        }:
            status = ValidationStatus.PENDING_REVIEW

        needs_review = self._needs_human_review(
            is_positive=is_positive,
            signals=signals,
            snapshot=snapshot,
            status=status,
            judge_result=judge_result,
        )
        reason = self._build_reason(
            feedback=feedback,
            classification=classification,
            signals=signals,
            credibility=credibility,
            status=status,
            judge_result=judge_result,
            is_positive=is_positive,
        )
        logger.info(reason)

        return ValidationResult(
            validation_status=status,
            credibility_score=round(credibility, 3),
            validation_reason=reason,
            feedback_category=classification.feedback_category,
            category_confidence=classification.category_confidence,
            detected_signals=signals,
            needs_human_review=needs_review,
            judge_result=judge_result,
            warnings=warnings,
        )

    def _classify(self, feedback: FeedbackInput) -> ClassificationResult:
        if feedback.rating >= 4 and not feedback.comment:
            return ClassificationResult(
                feedback_category=FeedbackCategory.POSITIVE_FEEDBACK,
                category_confidence=0.75,
            )
        return self._classifier.classify(feedback.comment, rating=feedback.rating)

    def _prepare_signals(
        self,
        feedback: FeedbackInput,
        implicit_signals: ImplicitSignals,
    ) -> dict[str, Any]:
        data = implicit_signals.model_dump()
        if (
            data.get("reformulation_detected") is None
            and data.get("reformulation_similarity") is not None
        ):
            data["reformulation_detected"] = (
                data["reformulation_similarity"]
                > self._config.FEEDBACK_REFORMULATION_SIMILARITY_THRESHOLD
            )
        if data.get("reformulation_similarity") is None and data.get("follow_up_question"):
            data["reformulation_similarity"] = SequenceMatcher(
                None,
                feedback.question.lower(),
                str(data["follow_up_question"]).lower(),
            ).ratio()
            data["reformulation_detected"] = (
                data["reformulation_similarity"]
                > self._config.FEEDBACK_REFORMULATION_SIMILARITY_THRESHOLD
            )
        return data

    def _positive_score(
        self,
        classification: ClassificationResult,
        feedback: FeedbackInput,
        signals: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> float:
        score = self._config.FEEDBACK_CREDIBILITY_BASE

        if signals.get("opened_sources") or signals.get("opened_details"):
            score *= self._config.POSITIVE_MULTIPLIER_OPENED_CONTEXT
        if signals.get("expanded_visualization"):
            score *= self._config.POSITIVE_MULTIPLIER_EXPANDED_VISUALIZATION
        if signals.get("exported_report"):
            score *= self._config.POSITIVE_MULTIPLIER_EXPORTED_REPORT
        if signals.get("copied_response"):
            score *= self._config.POSITIVE_MULTIPLIER_COPIED_RESPONSE
        if (
            classification.feedback_category == FeedbackCategory.POSITIVE_FEEDBACK
            and bool(feedback.comment and feedback.comment.strip())
        ):
            score *= self._config.POSITIVE_MULTIPLIER_EXPLICIT_COMMENT
        if _snapshot_has_user_facing_critical_issue(snapshot):
            score *= self._config.POSITIVE_MULTIPLIER_CRITICAL_WARNINGS

        score *= self._positive_dwell_multiplier(signals)
        return _clamp(score)

    def _base_score(
        self,
        classification: ClassificationResult,
        feedback: FeedbackInput,
        signals: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> float:
        score = self._config.FEEDBACK_CREDIBILITY_BASE
        has_comment = bool(feedback.comment and feedback.comment.strip())

        if classification.is_specific:
            score *= self._config.FEEDBACK_MULTIPLIER_SPECIFIC
        if classification.mentions_verifiable_element:
            score *= self._config.FEEDBACK_MULTIPLIER_VERIFIABLE
        if _snapshot_has_warnings(snapshot):
            score *= self._config.FEEDBACK_MULTIPLIER_RESPONSE_WARNINGS
        if _response_has_data_but_no_insights(snapshot):
            score *= self._config.FEEDBACK_MULTIPLIER_DATA_NO_INSIGHTS
        if _source_complaint_and_sources_missing(classification, snapshot):
            score *= self._config.FEEDBACK_MULTIPLIER_SOURCE_MISSING
        if signals.get("opened_sources") or signals.get("opened_details"):
            score *= self._config.FEEDBACK_MULTIPLIER_OPENED_CONTEXT
        if signals.get("reran_question"):
            score *= self._config.FEEDBACK_MULTIPLIER_RERAN_QUESTION
        if signals.get("reformulation_detected"):
            score *= self._config.FEEDBACK_MULTIPLIER_REFORMULATION

        score *= self._dwell_multiplier(signals)

        if (
            classification.feedback_category == FeedbackCategory.VAGUE_NEGATIVE
            and not has_comment
        ):
            score *= self._config.FEEDBACK_MULTIPLIER_VAGUE_NO_COMMENT
        elif feedback.rating <= 2 and not has_comment:
            score *= self._config.FEEDBACK_MULTIPLIER_NEGATIVE_NO_COMMENT

        if signals.get("copied_response") or signals.get("exported_report"):
            if not _copy_is_evidence_gathering(signals, classification):
                score *= self._config.FEEDBACK_MULTIPLIER_COPY_EXPORT_DISLIKE

        if _contradicts_stats_without_reason(classification, has_comment):
            score *= self._config.FEEDBACK_MULTIPLIER_CONTRADICTS_STATS

        return _clamp(score)

    def _dwell_multiplier(self, signals: dict[str, Any]) -> float:
        dwell = signals.get("dwell_time_ms")
        char_count = signals.get("response_char_count")
        if dwell is None or not char_count:
            return 1.0
        reading_speed = float(dwell) / max(float(char_count), 1.0)
        threshold = self._config.FEEDBACK_READING_SPEED_THRESHOLD_MS_PER_CHAR
        if reading_speed >= threshold:
            return 1.0
        ratio = max(0.0, reading_speed / threshold)
        min_multiplier = self._config.FEEDBACK_MIN_FAST_DWELL_MULTIPLIER
        return min_multiplier + (1.0 - min_multiplier) * ratio

    def _positive_dwell_multiplier(self, signals: dict[str, Any]) -> float:
        reading_speed = _reading_speed(signals)
        if reading_speed is None:
            return 1.0
        threshold = self._config.FEEDBACK_READING_SPEED_THRESHOLD_MS_PER_CHAR
        if reading_speed >= threshold:
            return 1.0
        ratio = max(0.0, reading_speed / threshold)
        min_multiplier = self._config.POSITIVE_MIN_FAST_DWELL_MULTIPLIER
        return min_multiplier + (1.0 - min_multiplier) * ratio

    def _should_run_judge(
        self,
        *,
        feedback: FeedbackInput,
        classification: ClassificationResult,
        response_snapshot: dict[str, Any],
        credibility_score: float,
    ) -> bool:
        if feedback.rating > 2:
            return False
        if not feedback.comment or not feedback.comment.strip():
            return False
        if not classification.is_specific:
            return False
        if not response_snapshot:
            return False
        if classification.feedback_category in {
            FeedbackCategory.OPINION_OR_EMOTION,
            FeedbackCategory.FORMATTING_OR_TONE,
            FeedbackCategory.VAGUE_NEGATIVE,
        }:
            return False
        if classification.feedback_category in _JUDGE_CATEGORIES:
            return True
        return self._config.NEGATIVE_PENDING_THRESHOLD <= credibility_score < (
            self._config.NEGATIVE_ACCEPTED_THRESHOLD
        )

    def _apply_judge(
        self,
        credibility_score: float,
        judge_result: JudgeResult,
    ) -> tuple[float, bool]:
        if judge_result.judge_verdict == "supports_user_feedback":
            return (
                _clamp(
                    credibility_score
                    * self._config.FEEDBACK_MULTIPLIER_JUDGE_SUPPORTS
                ),
                False,
            )
        if judge_result.judge_verdict == "contradicts_user_feedback":
            return (
                _clamp(
                    credibility_score
                    * self._config.FEEDBACK_MULTIPLIER_JUDGE_CONTRADICTS
                ),
                True,
            )
        return credibility_score, False

    def _status_for(self, score: float, *, is_positive: bool) -> ValidationStatus:
        accepted = (
            self._config.POSITIVE_ACCEPTED_THRESHOLD
            if is_positive
            else self._config.NEGATIVE_ACCEPTED_THRESHOLD
        )
        pending = (
            self._config.POSITIVE_PENDING_THRESHOLD
            if is_positive
            else self._config.NEGATIVE_PENDING_THRESHOLD
        )
        low_confidence = (
            self._config.POSITIVE_LOW_CONFIDENCE_THRESHOLD
            if is_positive
            else self._config.NEGATIVE_LOW_CONFIDENCE_THRESHOLD
        )
        if score >= accepted:
            return ValidationStatus.ACCEPTED
        if score >= pending:
            return ValidationStatus.PENDING_REVIEW
        if score >= low_confidence:
            return ValidationStatus.LOW_CONFIDENCE
        return ValidationStatus.REJECTED

    def _needs_human_review(
        self,
        *,
        is_positive: bool,
        signals: dict[str, Any],
        snapshot: dict[str, Any],
        status: ValidationStatus,
        judge_result: JudgeResult | None,
    ) -> bool:
        if is_positive:
            reading_speed = _reading_speed(signals)
            suspiciously_short = (
                reading_speed is not None
                and reading_speed
                < self._config.FEEDBACK_READING_SPEED_THRESHOLD_MS_PER_CHAR * 0.5
            )
            return suspiciously_short and _snapshot_has_user_facing_critical_issue(snapshot)

        return status == ValidationStatus.PENDING_REVIEW or (
            judge_result is not None and judge_result.judge_verdict == "inconclusive"
        )

    @staticmethod
    def _build_reason(
        *,
        feedback: FeedbackInput,
        classification: ClassificationResult,
        signals: dict[str, Any],
        credibility: float,
        status: ValidationStatus,
        judge_result: JudgeResult | None,
        is_positive: bool,
    ) -> str:
        if is_positive:
            engagement: list[str] = []
            if signals.get("opened_sources"):
                engagement.append("opened sources")
            if signals.get("opened_details"):
                engagement.append("details")
            if signals.get("expanded_visualization"):
                engagement.append("visualization")
            if signals.get("exported_report"):
                engagement.append("exported report")
            if signals.get("copied_response"):
                engagement.append("copied response")
            descriptor = (
                f"Positive feedback with strong engagement ({', '.join(engagement)})"
                if engagement
                else "Positive feedback"
            )
            parts = [descriptor]
            if signals.get("dwell_time_ms") is not None and signals.get("response_char_count"):
                parts.append(
                    "dwell="
                    f"{signals['dwell_time_ms']}ms/{signals['response_char_count']} chars"
                )
            parts.append(f"Credibility: {credibility:.2f} -> {status.value}")
            return ". ".join(parts) + "."

        parts = [
            f"{'Negative' if feedback.rating <= 2 else 'Neutral'} feedback",
            f"category={classification.feedback_category.value}",
        ]
        if classification.is_specific:
            parts.append("specific claim")
        if classification.mentions_verifiable_element:
            parts.append("mentions verifiable element")
        if signals.get("opened_sources") or signals.get("opened_details"):
            parts.append("user inspected context")
        if signals.get("reran_question"):
            parts.append("question rerun")
        if signals.get("reformulation_detected"):
            parts.append("reformulation detected")
        if signals.get("dwell_time_ms") is not None and signals.get("response_char_count"):
            parts.append(
                "dwell="
                f"{signals['dwell_time_ms']}ms/{signals['response_char_count']} chars"
            )
        if judge_result is not None:
            parts.append(
                f"judge={judge_result.judge_verdict}({judge_result.judge_confidence:.2f})"
            )
        parts.append(f"Credibility: {credibility:.2f} -> {status.value}")
        return ". ".join(parts) + "."


def _snapshot_has_warnings(snapshot: dict[str, Any]) -> bool:
    warnings = snapshot.get("warnings")
    errors = snapshot.get("errors") or snapshot.get("error")
    return bool(errors) or bool(warnings)


def _snapshot_has_user_facing_critical_issue(snapshot: dict[str, Any]) -> bool:
    if snapshot.get("failed_steps"):
        return True
    if snapshot.get("fallback_only") or snapshot.get("fallback_answer_only"):
        return True
    if _response_has_data_but_no_insights(snapshot):
        return True

    errors = snapshot.get("errors") or snapshot.get("error")
    error_text = str(errors or "").lower()
    if "sql" in error_text and any(
        marker in error_text for marker in ("fail", "error", "éch", "echec")
    ):
        return True

    warnings = snapshot.get("warnings") or []
    warning_text = " ".join(str(w) for w in warnings).lower()
    internal_markers = (
        "neo4j",
        "kg ",
        "cache",
        "execute_write",
        "merge insight",
        "échec merge insight",
        "echec merge insight",
    )
    if warning_text and not any(marker in warning_text for marker in internal_markers):
        if any(
            marker in warning_text
            for marker in ("no data", "aucune donnée", "sql", "failed", "échoué", "echec")
        ):
            return True

    data = snapshot.get("data")
    question_requires_data = bool(snapshot.get("analysis_stats") or snapshot.get("intent"))
    return question_requires_data and isinstance(data, list) and not data


def _response_has_data_but_no_insights(snapshot: dict[str, Any]) -> bool:
    data = snapshot.get("data")
    insights = snapshot.get("insights")
    return bool(data) and not bool(insights)


def _source_complaint_and_sources_missing(
    classification: ClassificationResult,
    snapshot: dict[str, Any],
) -> bool:
    if classification.feedback_category != FeedbackCategory.SOURCE_ERROR:
        return False
    response_mode = str(
        snapshot.get("response_mode")
        or snapshot.get("mode")
        or snapshot.get("provenance", {}).get("response_mode", "")
    ).lower()
    if response_mode not in {"external", "hybrid"}:
        return False
    sources = (
        snapshot.get("external_sources")
        or snapshot.get("sources")
        or snapshot.get("provenance", {}).get("external_sources")
    )
    return not bool(sources)


def _copy_is_evidence_gathering(
    signals: dict[str, Any],
    classification: ClassificationResult,
) -> bool:
    return signals.get("copy_zone") in {"sql", "data"} and (
        classification.feedback_category
        in {FeedbackCategory.TECHNICAL_ERROR, FeedbackCategory.DATA_ERROR}
        or classification.mentions_verifiable_element
    )


def _contradicts_stats_without_reason(
    classification: ClassificationResult,
    has_comment: bool,
) -> bool:
    return (
        classification.feedback_category == FeedbackCategory.DATA_ERROR
        and not classification.is_specific
        and has_comment
    )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _is_positive_feedback(feedback: FeedbackInput) -> bool:
    return feedback.feedback_type == FeedbackType.POSITIVE or feedback.rating >= 4


def _reading_speed(signals: dict[str, Any]) -> float | None:
    dwell = signals.get("dwell_time_ms")
    char_count = signals.get("response_char_count")
    if dwell is None or not char_count:
        return None
    return float(dwell) / max(float(char_count), 1.0)
