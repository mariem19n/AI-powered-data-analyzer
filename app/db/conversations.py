"""Persistent conversation history backed by PostgreSQL."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg


DEFAULT_TITLE = "Nouvelle conversation"
MAX_TITLE_LENGTH = 50


def make_conversation_title(question: str) -> str:
    """Create a compact title from the first user question."""
    title = " ".join((question or "").strip().split())
    if not title:
        return DEFAULT_TITLE
    if len(title) <= MAX_TITLE_LENGTH:
        return title
    return title[: MAX_TITLE_LENGTH - 3].rstrip() + "..."


def _row_to_conversation(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "user_id": row["user_id"],
        "title": row["title"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


def _decode_response_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return json.loads(value)
    return value


def _row_to_message(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "conversation_id": str(row["conversation_id"]),
        "role": row["role"],
        "content": row["content"],
        "response_json": _decode_response_json(row["response_json"]),
        "created_at": row["created_at"].isoformat(),
    }


async def init_conversation_tables(pool: asyncpg.Pool) -> None:
    """Create conversation tables when the app starts."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id UUID PRIMARY KEY,
                user_id TEXT NULL,
                title VARCHAR(255) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS conversation_messages (
                id UUID PRIMARY KEY,
                conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role VARCHAR(20) NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                response_json JSONB NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_conversations_user_updated
                ON conversations(user_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_conversation_messages_conversation_created
                ON conversation_messages(conversation_id, created_at ASC);
            """
        )


async def create_conversation(
    pool: asyncpg.Pool,
    *,
    user_id: str | None = None,
    title: str = DEFAULT_TITLE,
) -> dict[str, Any]:
    conversation_id = uuid4()
    now = datetime.utcnow()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO conversations (id, user_id, title, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $4)
            RETURNING id, user_id, title, created_at, updated_at;
            """,
            conversation_id,
            user_id,
            make_conversation_title(title),
            now,
        )
    return _row_to_conversation(row)


async def get_conversation(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    *,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, user_id, title, created_at, updated_at
            FROM conversations
            WHERE id = $1 AND ($2::text IS NULL OR user_id = $2)
            """,
            conversation_id,
            user_id,
        )
    return _row_to_conversation(row) if row else None


async def list_conversations(
    pool: asyncpg.Pool,
    *,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, user_id, title, created_at, updated_at
            FROM conversations
            WHERE ($1::text IS NULL OR user_id = $1)
            ORDER BY updated_at DESC;
            """,
            user_id,
        )
    return [_row_to_conversation(row) for row in rows]


async def rename_conversation(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    *,
    title: str,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE conversations
            SET title = $3, updated_at = NOW()
            WHERE id = $1 AND ($2::text IS NULL OR user_id = $2)
            RETURNING id, user_id, title, created_at, updated_at;
            """,
            conversation_id,
            user_id,
            make_conversation_title(title),
        )
    return _row_to_conversation(row) if row else None


async def delete_conversation(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    *,
    user_id: str | None = None,
) -> bool:
    async with pool.acquire() as conn:
        status = await conn.execute(
            """
            DELETE FROM conversations
            WHERE id = $1 AND ($2::text IS NULL OR user_id = $2);
            """,
            conversation_id,
            user_id,
        )
    return status.endswith(" 1")


async def list_messages(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    *,
    user_id: str | None = None,
) -> list[dict[str, Any]] | None:
    conversation = await get_conversation(pool, conversation_id, user_id=user_id)
    if not conversation:
        return None

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, conversation_id, role, content, response_json, created_at
            FROM conversation_messages
            WHERE conversation_id = $1
            ORDER BY created_at ASC;
            """,
            conversation_id,
        )
    return [_row_to_message(row) for row in rows]


async def add_message(
    pool: asyncpg.Pool,
    *,
    conversation_id: UUID,
    role: str,
    content: str,
    response_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response_json_text = json.dumps(response_json) if response_json is not None else None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO conversation_messages
                (id, conversation_id, role, content, response_json, created_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, NOW())
            RETURNING id, conversation_id, role, content, response_json, created_at;
            """,
            uuid4(),
            conversation_id,
            role,
            content,
            response_json_text,
        )
        await conn.execute(
            "UPDATE conversations SET updated_at = NOW() WHERE id = $1;",
            conversation_id,
        )
    return _row_to_message(row)
