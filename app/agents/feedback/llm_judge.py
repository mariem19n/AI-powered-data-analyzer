"""Optional LLM triangulation for specific negative feedback."""

from __future__ import annotations

import time
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.agents.feedback.config import FeedbackConfig, feedback_config
from app.agents.feedback.models import FeedbackInput, JudgeResult


class _JSONSchemaClient(Protocol):
    def chat_json_schema(
        self,
        *,
        system: str,
        user: str,
        schema: type[BaseModel],
        purpose: str,
        temperature: float,
        max_tokens: int,
    ) -> BaseModel:
        ...


class LLMJudgeResponse(BaseModel):
    """Strict JSON response expected from the LLM Judge."""

    judge_verdict: str = Field(
        description="supports_user_feedback | contradicts_user_feedback | inconclusive"
    )
    judge_confidence: float = Field(ge=0.0, le=1.0)
    judge_reason: str
    error_type: str


class LLMJudge:
    """Run a bounded LLM faithfulness check for selected feedback."""

    def __init__(
        self,
        *,
        client: _JSONSchemaClient | None = None,
        config: FeedbackConfig | None = None,
    ) -> None:
        self._client = client
        self._config = config or feedback_config
        self._window_started_at = time.time()
        self._calls_in_window = 0

    def can_run(self) -> bool:
        """Return whether the hourly judge rate limit allows another call."""
        now = time.time()
        if now - self._window_started_at >= 3600:
            self._window_started_at = now
            self._calls_in_window = 0
        return self._calls_in_window < self._config.LLM_JUDGE_MAX_PER_HOUR

    def judge(
        self,
        *,
        feedback: FeedbackInput,
        response_snapshot: dict[str, Any],
    ) -> tuple[JudgeResult | None, list[str]]:
        """Ask the LLM to compare feedback against the provided response context."""
        warnings: list[str] = []
        if self._client is None:
            warnings.append("LLM Judge skipped: no client configured")
            return None, warnings
        if not self.can_run():
            warnings.append("LLM Judge skipped: rate limit reached")
            return None, warnings

        self._calls_in_window += 1
        try:
            raw = self._client.chat_json_schema(
                system=_SYSTEM_PROMPT,
                user=_build_user_prompt(feedback, response_snapshot),
                schema=LLMJudgeResponse,
                purpose="feedback_judge",
                temperature=0.0,
                max_tokens=500,
            )
            parsed = LLMJudgeResponse.model_validate(raw)
            return JudgeResult(**parsed.model_dump()), warnings
        except Exception as exc:  # noqa: BLE001 - non-blocking judge failure
            warnings.append(f"LLM Judge failed: {exc}")
            return None, warnings


_SYSTEM_PROMPT = (
    "You are a strict feedback judge. Compare the assistant response only "
    "against the provided computed stats, SQL, sources and provenance. Do not "
    "recompute numbers. Return JSON matching the schema."
)


def _compact(value: Any, max_chars: int = 6000) -> str:
    text = str(value)
    return text if len(text) <= max_chars else text[:max_chars] + "...[truncated]"


def _build_user_prompt(feedback: FeedbackInput, response_snapshot: dict[str, Any]) -> str:
    """Build a compact judge prompt from already-computed context."""
    return "\n".join(
        [
            f"Original question: {feedback.question}",
            f"Detected intent: {feedback.intent or ''}",
            f"User feedback: {feedback.comment or ''}",
            f"Response mode: {response_snapshot.get('response_mode') or response_snapshot.get('mode') or ''}",
            f"Insights: {_compact(response_snapshot.get('insights'))}",
            f"Analysis stats: {_compact(response_snapshot.get('analysis_stats'))}",
            f"SQL/data provenance: {_compact(response_snapshot.get('provenance'))}",
            f"Sources: {_compact(response_snapshot.get('external_sources') or response_snapshot.get('sources'))}",
        ]
    )
