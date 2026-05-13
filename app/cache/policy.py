"""
Policy for deciding whether a final orchestrator response is safe to cache.

The policy is deliberately conservative: a response is cached only when it is
internal, complete, backed by non-empty SQL data, and based on a confident
semantic context.
"""

from __future__ import annotations

from typing import Any


PLACEHOLDER_MARKERS = (
    "placeholder",
    "analyse non disponible",
    "analysis unavailable",
)

SHORT_TTL_INTENTS = {"forecasting", "anomaly_detection", "correlation"}
DEFAULT_TTL_INTENTS = {"aggregation", "comparison"}


def _iter_semantic_contexts(response: dict[str, Any]) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []

    direct = response.get("semantic_context")
    if isinstance(direct, dict):
        contexts.append(direct)

    plan = response.get("plan")
    steps = plan.get("steps", []) if isinstance(plan, dict) else []
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            instruction = step.get("instruction", {})
            if not isinstance(instruction, dict):
                continue
            ctx = instruction.get("semantic_context")
            if isinstance(ctx, dict):
                contexts.append(ctx)

    return contexts


def has_non_empty_data(response: dict[str, Any]) -> bool:
    data = response.get("data")
    if not isinstance(data, list) or not data:
        return False

    saw_sql_result = False
    for item in data:
        if not isinstance(item, dict):
            continue

        records = item.get("records")
        row_count = item.get("row_count")
        if row_count is not None:
            saw_sql_result = True
            if not isinstance(row_count, int) or row_count <= 0:
                return False

        if records is not None:
            saw_sql_result = True
            if not isinstance(records, list) or not records:
                return False

    return saw_sql_result


def has_placeholder_insights(response: dict[str, Any]) -> bool:
    insights = response.get("insights", [])
    if not isinstance(insights, list):
        return False

    for insight in insights:
        text = str(insight).lower()
        if any(marker in text for marker in PLACEHOLDER_MARKERS):
            return True
    return False


def has_failed_steps(response: dict[str, Any]) -> bool:
    failed_steps = response.get("failed_steps", [])
    return isinstance(failed_steps, list) and bool(failed_steps)


def has_valid_semantic_context(response: dict[str, Any]) -> bool:
    contexts = _iter_semantic_contexts(response)
    if not contexts:
        return False

    for ctx in contexts:
        if ctx.get("needs_clarification") is True:
            return False
        unknown_terms = ctx.get("unknown_terms", [])
        if isinstance(unknown_terms, list) and unknown_terms:
            return False
        confidence = ctx.get("confidence", 0.0)
        if not isinstance(confidence, (int, float)) or confidence < 0.75:
            return False

    return True


def should_cache_response(response: dict[str, Any]) -> bool:
    if response.get("needs_clarification") is True:
        return False
    if response.get("partial") is True:
        return False
    if response.get("response_mode") != "internal":
        return False
    if has_failed_steps(response):
        return False
    if not has_non_empty_data(response):
        return False
    if has_placeholder_insights(response):
        return False
    if not has_valid_semantic_context(response):
        return False
    return True


def get_cache_ttl(response: dict[str, Any]) -> int:
    if response.get("response_mode") != "internal":
        return 300

    plan = response.get("plan", {})
    intent = plan.get("intent") if isinstance(plan, dict) else None
    if intent in DEFAULT_TTL_INTENTS:
        return 3600
    if intent in SHORT_TTL_INTENTS:
        return 900
    return 1800
