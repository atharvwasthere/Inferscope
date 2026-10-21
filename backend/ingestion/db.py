import json
import os
from collections.abc import AsyncGenerator
from uuid import UUID

import asyncpg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]

_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=DATABASE_URL,
            min_size=2,
            max_size=10,
            command_timeout=30,
            max_inactive_connection_lifetime=300,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool | None:
    """The pool itself, for callers outside a request scope (the drains, the outbox)."""
    return _pool


async def get_db() -> AsyncGenerator[asyncpg.Connection, None]:
    assert _pool is not None, "pool not initialized"
    async with _pool.acquire() as conn:
        yield conn


async def get_provider_rates(
    db: asyncpg.Connection, provider: str, model: str
) -> tuple[float, float] | None:
    row = await db.fetchrow(
        """
        SELECT cost_per_input_token, cost_per_output_token
        FROM provider_models
        WHERE provider = $1 AND model = $2
        """,
        provider,
        model,
    )
    if row is None:
        return None
    return float(row["cost_per_input_token"]), float(row["cost_per_output_token"])


async def insert_inference_log(db: asyncpg.Connection, row: dict) -> UUID | None:
    return await db.fetchval(
        """
        INSERT INTO inference_logs (
            request_id, trace_id, conversation_id, message_id, provider, model,
            latency_ms, time_to_first_token_ms,
            input_tokens, output_tokens, total_tokens,
            cache_read_input_tokens, cache_creation_input_tokens, reasoning_tokens,
            cost_usd, status, error_message,
            input_preview, output_preview,
            raw_usage, attributes,
            started_at
        )
        VALUES (
            $1, $2, $3, $4, $5, $6,
            $7, $8,
            $9, $10, $11,
            $12, $13, $14,
            $15, $16, $17,
            $18, $19,
            $20, $21,
            $22
        )
        ON CONFLICT (request_id) DO NOTHING
        RETURNING id
        """,
        row["request_id"],
        row.get("trace_id"),
        row.get("conversation_id"),
        row.get("message_id"),
        row["provider"],
        row["model"],
        row.get("latency_ms"),
        row.get("time_to_first_token_ms"),
        row.get("input_tokens", 0),
        row.get("output_tokens", 0),
        row.get("total_tokens"),
        row.get("cache_read_input_tokens", 0),
        row.get("cache_creation_input_tokens", 0),
        row.get("reasoning_tokens", 0),
        row.get("cost_usd"),
        row["status"],
        row.get("error_message"),
        row.get("input_preview"),
        row.get("output_preview"),
        json.dumps(row["raw_usage"]) if row.get("raw_usage") else None,
        json.dumps(row["attributes"]) if row.get("attributes") else None,
        row.get("started_at"),
    )
