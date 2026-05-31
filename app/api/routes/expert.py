"""Expert supervision API routes.

Read-only supervision endpoints plus lightweight feedback review updates.
These routes do not run agents and keep Neo4j best-effort/read-only.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.db.neo4j import ALLOWED_LABELS, neo4j_driver

router = APIRouter(prefix="/api/expert", tags=["expert"])


def _json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            import json

            return json.loads(value)
        except Exception:
            return value
    return value


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


def _date_value(value: Any):
    if value is None:
        return None
    if hasattr(value, "date"):
        return value.date()
    return value


def _preview(snapshot: dict[str, Any] | None, fallback: str | None = None) -> str:
    if not isinstance(snapshot, dict):
        return fallback or ""
    for key in ("answer", "summary"):
        if isinstance(snapshot.get(key), str) and snapshot[key].strip():
            return snapshot[key].strip()[:420]
    insights = snapshot.get("insights")
    if isinstance(insights, list):
        text = " ".join(str(item).strip() for item in insights if str(item).strip())
        if text:
            return text[:420]
    return fallback or ""


async def _table_exists(conn: Any, table_name: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT to_regclass($1)::text IS NOT NULL;",
            table_name,
        )
    )


async def _column_exists(conn: Any, table: str, column: str) -> bool:
    return bool(
        await conn.fetchval(
            """
            SELECT EXISTS (
              SELECT 1 FROM information_schema.columns
              WHERE table_name = $1 AND column_name = $2
            );
            """,
            table,
            column,
        )
    )


class ExpertReviewRequest(BaseModel):
    decision: str = Field(pattern="^(accepted|rejected|pending_review)$")
    expert_score: float | None = Field(default=None, ge=0.0, le=1.0)
    expert_note: str | None = None


@router.get("/feedback-queue")
async def feedback_queue(role: str | None = Query(default=None)) -> dict[str, Any]:
    """Return pending feedbacks and compact queue counters."""
    from app.db.postgres import pg_pool

    async with pg_pool.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                id, conversation_id, message_id, response_id, session_id,
                question, intent, rating, comment, feedback_type,
                response_snapshot, implicit_signals, feedback_category,
                category_confidence, credibility_score, validation_status,
                validation_reason, detected_signals, judge_result,
                needs_human_review, expert_score, expert_note,
                expert_reviewed_at, created_at
            FROM user_feedback
            WHERE COALESCE(validation_status, 'pending_review') = 'pending_review'
            ORDER BY
                ABS(COALESCE(credibility_score, 0.65) - 0.65) ASC,
                created_at DESC
            LIMIT 100;
            """
        )
        counts = await conn.fetchrow(
            """
            SELECT
              COUNT(*) FILTER (WHERE COALESCE(validation_status, 'pending_review') = 'pending_review') AS pending,
              COUNT(*) FILTER (WHERE validation_status = 'accepted' AND created_at::date = CURRENT_DATE) AS accepted_today,
              COUNT(*) FILTER (WHERE validation_status = 'rejected' AND created_at::date = CURRENT_DATE) AS rejected_today
            FROM user_feedback;
            """
        )

    items = []
    for row in rows:
        snapshot = _json(row["response_snapshot"])
        items.append(
            {
                "feedback_id": str(row["id"]),
                "created_at": _iso(row["created_at"]),
                "question": row["question"],
                "user_rating": row["rating"],
                "user_comment": row["comment"],
                "feedback_type": row["feedback_type"],
                "validation_status": row["validation_status"],
                "validator_verdict": row["validation_reason"],
                "validator_score": row["credibility_score"],
                "feedback_category": row["feedback_category"],
                "implicit_signals": _json(row["implicit_signals"]),
                "detected_signals": _json(row["detected_signals"]),
                "judge_result": _json(row["judge_result"]),
                "response_summary": _preview(snapshot),
                "response_snapshot": snapshot,
                "message_id": str(row["message_id"]) if row["message_id"] else None,
                "conversation_id": str(row["conversation_id"]) if row["conversation_id"] else None,
                "intent": row["intent"],
                "expert_score": row["expert_score"],
                "expert_note": row["expert_note"],
                "needs_human_review": row["needs_human_review"],
            }
        )

    return {
        "counts": {
            "pending": counts["pending"] if counts else 0,
            "accepted_today": counts["accepted_today"] if counts else 0,
            "rejected_today": counts["rejected_today"] if counts else 0,
        },
        "items": items,
    }


@router.post("/feedback/{feedback_id}/review")
async def review_feedback(feedback_id: UUID, payload: ExpertReviewRequest) -> dict[str, Any]:
    """Update expert review fields for a feedback row."""
    from app.db.postgres import pg_pool

    async with pg_pool.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE user_feedback
            SET validation_status = $2,
                expert_score = $3,
                expert_note = $4,
                expert_reviewed_at = NOW(),
                needs_human_review = CASE WHEN $2 = 'pending_review' THEN true ELSE false END
            WHERE id = $1
            RETURNING id, validation_status, expert_score, expert_note, expert_reviewed_at;
            """,
            feedback_id,
            payload.decision,
            payload.expert_score,
            payload.expert_note,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Feedback not found")
    return {
        "success": True,
        "feedback_id": str(row["id"]),
        "validation_status": row["validation_status"],
        "expert_score": row["expert_score"],
        "expert_note": row["expert_note"],
        "expert_reviewed_at": _iso(row["expert_reviewed_at"]),
    }


@router.get("/response-quality")
async def response_quality(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
    """Return recent assistant responses and quality metadata when available."""
    from app.db.postgres import pg_pool

    async with pg_pool.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
              m.id AS message_id,
              m.conversation_id,
              m.content,
              m.response_json,
              m.created_at,
              uf.rating,
              uf.validation_status
            FROM conversation_messages m
            LEFT JOIN LATERAL (
              SELECT rating, validation_status
              FROM user_feedback
              WHERE message_id = m.id
              ORDER BY created_at DESC
              LIMIT 1
            ) uf ON true
            WHERE m.role = 'assistant'
            ORDER BY m.created_at DESC
            LIMIT $1;
            """,
            limit,
        )

    items = []
    for row in rows:
        response = _json(row["response_json"]) or {}
        metadata = response.get("metadata") or {}
        llm_trace = response.get("llm_trace") or []
        warnings = response.get("warnings") or []
        data = response.get("data") or []
        insights = response.get("insights") or []
        items.append(
            {
                "message_id": str(row["message_id"]),
                "created_at": _iso(row["created_at"]),
                "question": response.get("question"),
                "intent": (response.get("intent") or {}).get("primary")
                if isinstance(response.get("intent"), dict)
                else response.get("intent"),
                "total_duration_s": metadata.get("duration_s") or metadata.get("total_duration_s"),
                "llm_calls": len(llm_trace) if isinstance(llm_trace, list) else None,
                "cache_hit": bool(metadata.get("cache_hit")) if metadata else False,
                "warnings_count": len(warnings) if isinstance(warnings, list) else 0,
                "data_rows": len(data) if isinstance(data, list) else None,
                "insights_count": len(insights) if isinstance(insights, list) else None,
                "user_rating": row["rating"],
                "validation_status": row["validation_status"],
                "detail": {
                    "plan": response.get("plan"),
                    "sql": response.get("sql") or response.get("sql_queries"),
                    "stats": response.get("analysis_stats"),
                    "insights": insights,
                    "visualizations": [
                        {"type": v.get("type"), "title": (v.get("layout") or {}).get("title")}
                        for v in (response.get("visualizations") or [])
                        if isinstance(v, dict)
                    ],
                    "warnings": warnings,
                },
            }
        )
    return {"items": items}


@router.get("/kg-stats")
async def kg_stats() -> dict[str, Any]:
    """Return Knowledge Graph read-only stats."""
    try:
        counts = {}
        for label in sorted(ALLOWED_LABELS):
            result = neo4j_driver.run_query(f"MATCH (n:{label}) RETURN count(n) AS count")
            counts[label] = result[0]["count"] if result else 0
        rel_result = neo4j_driver.run_query("MATCH ()-[r]->() RETURN count(r) AS count")
        low_confidence = neo4j_driver.run_query(
            """
            MATCH (n)
            WHERE n.confidence IS NOT NULL AND toFloat(n.confidence) < 0.5
            RETURN labels(n) AS labels, properties(n) AS properties
            LIMIT 20
            """
        )
        plausible = neo4j_driver.run_query(
            """
            MATCH (n)
            WHERE n.status = 'PLAUSIBLE_BUT_NEW' OR n.validation_status = 'PLAUSIBLE_BUT_NEW'
            RETURN labels(n) AS labels, properties(n) AS properties
            LIMIT 20
            """
        )
        return {
            "available": True,
            "total_nodes": sum(counts.values()),
            "total_relationships": rel_result[0]["count"] if rel_result else 0,
            "node_counts": counts,
            "low_confidence_terms": low_confidence,
            "plausible_but_new": plausible,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "message": f"Neo4j unavailable: {type(exc).__name__}",
            "total_nodes": 0,
            "total_relationships": 0,
            "node_counts": {},
            "low_confidence_terms": [],
            "plausible_but_new": [],
        }


@router.get("/prompt-performance")
async def prompt_performance() -> dict[str, Any]:
    """Aggregate stored llm_trace entries by purpose when present."""
    from app.db.postgres import pg_pool

    by_purpose: dict[str, dict[str, Any]] = {}
    async with pg_pool.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT response_json
            FROM conversation_messages
            WHERE role = 'assistant'
              AND response_json ? 'llm_trace'
            ORDER BY created_at DESC
            LIMIT 500;
            """
        )
    for row in rows:
        response = _json(row["response_json"]) or {}
        trace = response.get("llm_trace")
        if not isinstance(trace, list):
            continue
        for call in trace:
            if not isinstance(call, dict):
                continue
            purpose = str(call.get("purpose") or call.get("task") or "unknown")
            bucket = by_purpose.setdefault(
                purpose,
                {"purpose": purpose, "calls": 0, "latency_sum": 0.0, "tokens_sum": 0.0, "warnings": 0, "fallbacks": 0},
            )
            bucket["calls"] += 1
            bucket["latency_sum"] += float(call.get("latency_ms") or call.get("duration_ms") or 0)
            bucket["tokens_sum"] += float(call.get("tokens") or call.get("total_tokens") or 0)
            bucket["warnings"] += len(call.get("warnings") or []) if isinstance(call.get("warnings"), list) else 0
            bucket["fallbacks"] += 1 if call.get("fallback") or call.get("used_fallback") else 0
    items = []
    for bucket in by_purpose.values():
        calls = bucket["calls"] or 1
        items.append(
            {
                "purpose": bucket["purpose"],
                "calls": bucket["calls"],
                "avg_latency_ms": round(bucket["latency_sum"] / calls, 2),
                "avg_tokens": round(bucket["tokens_sum"] / calls, 2),
                "warnings": bucket["warnings"],
                "fallback_rate": round(bucket["fallbacks"] / calls, 3),
            }
        )
    return {"items": sorted(items, key=lambda item: item["calls"], reverse=True)}


@router.get("/data-freshness")
async def data_freshness() -> dict[str, Any]:
    """Return freshness for known source tables, without failing on missing tables."""
    from app.db.postgres import pg_pool

    sources = [
        ("Crypto", "fact_crypto_daily", "date"),
        ("FRED", "fact_fred_observation", "date"),
        ("GDELT", "stg_gdelt_sentiment", "date"),
        ("GDELT enriched", "agg_daily_sentiment", "date"),
    ]
    items = []
    now = datetime.now(timezone.utc).date()
    async with pg_pool.pool.acquire() as conn:
        for source_name, table_name, date_col in sources:
            if not await _table_exists(conn, table_name):
                items.append({"source_name": source_name, "table_name": table_name, "latest_date": None, "age_days": None, "status": "unknown", "record_count": None})
                continue
            if not await _column_exists(conn, table_name, date_col):
                items.append({"source_name": source_name, "table_name": table_name, "latest_date": None, "age_days": None, "status": "unknown", "record_count": None})
                continue
            row = await conn.fetchrow(
                f"SELECT MAX({date_col}) AS latest_date, COUNT(*) AS record_count FROM {table_name};"
            )
            latest = row["latest_date"] if row else None
            latest_date = _date_value(latest)
            age_days = (now - latest_date).days if latest_date else None
            status = "unknown"
            if age_days is not None:
                status = "green" if age_days <= 1 else "orange" if age_days <= 3 else "red"
            items.append({"source_name": source_name, "table_name": table_name, "latest_date": _iso(latest), "age_days": age_days, "status": status, "record_count": row["record_count"] if row else None})
    return {"items": items}


@router.get("/semantic-health")
async def semantic_health() -> dict[str, Any]:
    """Extract frequent unknown terms and analytic gaps from stored responses."""
    from app.db.postgres import pg_pool

    counter: Counter[tuple[str, str]] = Counter()
    examples: dict[tuple[str, str], dict[str, Any]] = {}
    async with pg_pool.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT response_json, created_at
            FROM conversation_messages
            WHERE role = 'assistant'
            ORDER BY created_at DESC
            LIMIT 500;
            """
        )
    for row in rows:
        response = _json(row["response_json"]) or {}
        question = response.get("question")
        contexts = []
        if isinstance(response.get("semantic_context"), dict):
            contexts.append(response["semantic_context"])
        plan = response.get("plan") or {}
        for step in plan.get("steps", []) if isinstance(plan, dict) else []:
            instruction = step.get("instruction") if isinstance(step, dict) else None
            if isinstance(instruction, dict) and isinstance(instruction.get("semantic_context"), dict):
                contexts.append(instruction["semantic_context"])
        for ctx in contexts:
            for term in ctx.get("unknown_terms") or []:
                key = ("unknown_term", str(term))
                counter[key] += 1
                examples.setdefault(key, {"example_question": question, "last_seen": _iso(row["created_at"])})
            for gap in ctx.get("analytic_gaps") or []:
                text = gap.get("label") if isinstance(gap, dict) else gap
                key = ("analytic_gap", str(text))
                counter[key] += 1
                examples.setdefault(key, {"example_question": question, "last_seen": _iso(row["created_at"])})
    items = [
        {"type": kind, "term": term, "count": count, **examples.get((kind, term), {})}
        for (kind, term), count in counter.most_common(50)
        if term and term != "None"
    ]
    return {"items": items}
