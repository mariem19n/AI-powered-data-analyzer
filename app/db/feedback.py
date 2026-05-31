"""PostgreSQL persistence for user feedback."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import asyncpg


def _to_uuid(value: Any) -> UUID | None:
    if value in (None, ""):
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _decode_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return json.loads(value)
    return value


def _row_to_feedback(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "conversation_id": str(row["conversation_id"]) if row["conversation_id"] else None,
        "message_id": str(row["message_id"]) if row["message_id"] else None,
        "response_id": row["response_id"],
        "session_id": row["session_id"],
        "question": row["question"],
        "intent": row["intent"],
        "rating": row["rating"],
        "comment": row["comment"],
        "feedback_type": row["feedback_type"],
        "response_snapshot": _decode_json(row["response_snapshot"]),
        "implicit_signals": _decode_json(row["implicit_signals"]),
        "feedback_category": row["feedback_category"],
        "category_confidence": row["category_confidence"],
        "credibility_score": row["credibility_score"],
        "validation_status": row["validation_status"],
        "validation_reason": row["validation_reason"],
        "detected_signals": _decode_json(row["detected_signals"]),
        "judge_result": _decode_json(row["judge_result"]),
        "needs_human_review": row["needs_human_review"],
        "created_at": row["created_at"].isoformat(),
    }


async def init_feedback_tables(pool: asyncpg.Pool) -> None:
    """Create the MVP feedback table."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_feedback (
                id UUID PRIMARY KEY,
                conversation_id UUID NULL REFERENCES conversations(id) ON DELETE SET NULL,
                message_id UUID NULL REFERENCES conversation_messages(id) ON DELETE SET NULL,
                response_id TEXT NULL,
                session_id TEXT NULL,
                question TEXT NULL,
                intent TEXT NULL,
                rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
                comment TEXT NULL,
                feedback_type TEXT NOT NULL DEFAULT 'rating',
                response_snapshot JSONB NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_user_feedback_conversation
                ON user_feedback(conversation_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_user_feedback_message
                ON user_feedback(message_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_user_feedback_rating
                ON user_feedback(rating, created_at DESC);
            """
        )
        await conn.execute(
            """
            ALTER TABLE user_feedback
                ADD COLUMN IF NOT EXISTS implicit_signals JSONB,
                ADD COLUMN IF NOT EXISTS feedback_category TEXT,
                ADD COLUMN IF NOT EXISTS category_confidence DOUBLE PRECISION,
                ADD COLUMN IF NOT EXISTS credibility_score DOUBLE PRECISION,
                ADD COLUMN IF NOT EXISTS validation_status TEXT DEFAULT 'pending_review',
                ADD COLUMN IF NOT EXISTS validation_reason TEXT,
                ADD COLUMN IF NOT EXISTS detected_signals JSONB,
                ADD COLUMN IF NOT EXISTS judge_result JSONB,
                ADD COLUMN IF NOT EXISTS needs_human_review BOOLEAN DEFAULT false,
                ADD COLUMN IF NOT EXISTS expert_score DOUBLE PRECISION,
                ADD COLUMN IF NOT EXISTS expert_note TEXT,
                ADD COLUMN IF NOT EXISTS expert_reviewed_at TIMESTAMPTZ;

            CREATE INDEX IF NOT EXISTS idx_user_feedback_validation_status
                ON user_feedback(validation_status, created_at DESC);
            """
        )


async def insert_user_feedback(
    pool: asyncpg.Pool,
    *,
    conversation_id: Any = None,
    message_id: Any = None,
    response_id: str | None = None,
    session_id: str | None = None,
    question: str | None = None,
    intent: str | None = None,
    rating: int,
    comment: str | None = None,
    feedback_type: str = "rating",
    response_snapshot: dict[str, Any] | None = None,
    implicit_signals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert a feedback row and return it as a JSON-compatible dict."""
    feedback_id = uuid4()
    snapshot_text = (
        json.dumps(response_snapshot, default=str)
        if response_snapshot is not None
        else None
    )
    signals_text = (
        json.dumps(implicit_signals, default=str)
        if implicit_signals is not None
        else None
    )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO user_feedback (
                id,
                conversation_id,
                message_id,
                response_id,
                session_id,
                question,
                intent,
                rating,
                comment,
                feedback_type,
                response_snapshot,
                implicit_signals,
                created_at
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12::jsonb, NOW()
            )
            RETURNING
                id,
                conversation_id,
                message_id,
                response_id,
                session_id,
                question,
                intent,
                rating,
                comment,
                feedback_type,
                response_snapshot,
                implicit_signals,
                feedback_category,
                category_confidence,
                credibility_score,
                validation_status,
                validation_reason,
                detected_signals,
                judge_result,
                needs_human_review,
                created_at;
            """,
            feedback_id,
            _to_uuid(conversation_id),
            _to_uuid(message_id),
            response_id,
            session_id,
            question,
            intent,
            rating,
            comment,
            feedback_type or "rating",
            snapshot_text,
            signals_text,
        )
    return _row_to_feedback(row)


async def update_user_feedback_validation(
    pool: asyncpg.Pool,
    *,
    feedback_id: Any,
    feedback_category: str | None,
    category_confidence: float | None,
    credibility_score: float | None,
    validation_status: str,
    validation_reason: str | None,
    detected_signals: dict[str, Any] | None,
    judge_result: dict[str, Any] | None,
    needs_human_review: bool,
) -> dict[str, Any]:
    """Update validation fields for an existing feedback row."""
    detected_text = (
        json.dumps(detected_signals, default=str)
        if detected_signals is not None
        else None
    )
    judge_text = (
        json.dumps(judge_result, default=str)
        if judge_result is not None
        else None
    )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE user_feedback
            SET feedback_category = $2,
                category_confidence = $3,
                credibility_score = $4,
                validation_status = $5,
                validation_reason = $6,
                detected_signals = $7::jsonb,
                judge_result = $8::jsonb,
                needs_human_review = $9
            WHERE id = $1
            RETURNING
                id,
                conversation_id,
                message_id,
                response_id,
                session_id,
                question,
                intent,
                rating,
                comment,
                feedback_type,
                response_snapshot,
                implicit_signals,
                feedback_category,
                category_confidence,
                credibility_score,
                validation_status,
                validation_reason,
                detected_signals,
                judge_result,
                needs_human_review,
                created_at;
            """,
            _to_uuid(feedback_id),
            feedback_category,
            category_confidence,
            credibility_score,
            validation_status,
            validation_reason,
            detected_text,
            judge_text,
            needs_human_review,
        )
    if row is None:
        raise ValueError(f"Feedback row not found: {feedback_id}")
    return _row_to_feedback(row)
