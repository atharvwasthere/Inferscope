import os
from typing import AsyncGenerator
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


async def get_db() -> AsyncGenerator[asyncpg.Connection, None]:
    assert _pool is not None, "pool not initialized"
    async with _pool.acquire() as conn:
        yield conn


# ---- query construction helpers ----------------------------------------

def _filters(provider: str | None, model: str | None, start: int = 1) -> tuple[str, list, int]:
    """Build parameterized provider/model filter clauses.

    sql_interval is whitelisted and interpolated by the caller; provider/model
    are always passed as bind parameters ($N) — never interpolated.
    """
    clauses: list[str] = []
    params: list = []
    idx = start
    if provider:
        clauses.append(f"AND provider = ${idx}")
        params.append(provider)
        idx += 1
    if model:
        clauses.append(f"AND model = ${idx}")
        params.append(model)
        idx += 1
    return " ".join(clauses), params, idx


# ---- repository functions ------------------------------------------------

async def get_summary(db, sql_interval: str, provider: str | None, model: str | None) -> dict:
    where, params, _ = _filters(provider, model)
    row = await db.fetchrow(
        f"""
        SELECT
            COUNT(*)                                                   AS total_requests,
            COUNT(*) FILTER (WHERE status = 'error')                   AS errors,
            COALESCE(SUM(cost_usd), 0)                                 AS total_cost_usd,
            COALESCE(AVG(latency_ms) FILTER (WHERE status = 'success'), 0) AS avg_latency_ms,
            COALESCE(SUM(total_tokens), 0)                             AS total_tokens
        FROM inference_logs
        WHERE created_at >= NOW() - INTERVAL '{sql_interval}' {where}
        """,
        *params,
    )
    return dict(row)


async def get_latency(db, sql_interval: str, provider: str | None, model: str | None) -> dict:
    where, params, _ = _filters(provider, model)
    row = await db.fetchrow(
        f"""
        SELECT
            percentile_cont(0.5)  WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
            AVG(latency_ms)                                          AS avg_ms,
            AVG(time_to_first_token_ms)                              AS avg_ttft_ms
        FROM inference_logs
        WHERE status = 'success' AND latency_ms IS NOT NULL
          AND created_at >= NOW() - INTERVAL '{sql_interval}' {where}
        """,
        *params,
    )
    return dict(row)


async def get_throughput(db, sql_interval: str, bucket: str, provider: str | None, model: str | None) -> list[dict]:
    where, params, _ = _filters(provider, model)
    rows = await db.fetch(
        f"""
        SELECT date_trunc('{bucket}', created_at) AS bucket,
               COUNT(*)                            AS count
        FROM inference_logs
        WHERE created_at >= NOW() - INTERVAL '{sql_interval}' {where}
        GROUP BY bucket
        ORDER BY bucket ASC
        """,
        *params,
    )
    return [dict(r) for r in rows]


async def get_latency_series(db, sql_interval: str, bucket: str, provider: str | None, model: str | None) -> list[dict]:
    """p50/p95 latency per time bucket — feeds the latency line chart."""
    where, params, _ = _filters(provider, model)
    rows = await db.fetch(
        f"""
        SELECT date_trunc('{bucket}', created_at) AS bucket,
               percentile_cont(0.5)  WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms
        FROM inference_logs
        WHERE status = 'success' AND latency_ms IS NOT NULL
          AND created_at >= NOW() - INTERVAL '{sql_interval}' {where}
        GROUP BY bucket
        ORDER BY bucket ASC
        """,
        *params,
    )
    return [dict(r) for r in rows]


async def get_error_series(db, sql_interval: str, bucket: str, provider: str | None, model: str | None) -> list[dict]:
    """Error rate (%) per time bucket — feeds the error-rate line chart."""
    where, params, _ = _filters(provider, model)
    rows = await db.fetch(
        f"""
        SELECT date_trunc('{bucket}', created_at)                       AS bucket,
               COUNT(*)                                                 AS total,
               COUNT(*) FILTER (WHERE status = 'error')                 AS errors,
               ROUND(
                   COUNT(*) FILTER (WHERE status = 'error')::numeric
                   / NULLIF(COUNT(*), 0) * 100, 2
               )                                                        AS error_rate_pct
        FROM inference_logs
        WHERE created_at >= NOW() - INTERVAL '{sql_interval}' {where}
        GROUP BY bucket
        ORDER BY bucket ASC
        """,
        *params,
    )
    return [dict(r) for r in rows]


async def get_errors(db, sql_interval: str, provider: str | None, model: str | None) -> dict:
    where, params, _ = _filters(provider, model)
    row = await db.fetchrow(
        f"""
        SELECT COUNT(*)                                  AS total,
               COUNT(*) FILTER (WHERE status = 'error')  AS errors
        FROM inference_logs
        WHERE created_at >= NOW() - INTERVAL '{sql_interval}' {where}
        """,
        *params,
    )
    return dict(row)


async def get_cost(db, sql_interval: str, provider: str | None, model: str | None) -> list[dict]:
    where, params, _ = _filters(provider, model)
    rows = await db.fetch(
        f"""
        SELECT provider, model,
               COALESCE(SUM(cost_usd), 0)     AS cost_usd,
               COUNT(*)                        AS requests,
               COALESCE(SUM(total_tokens), 0)  AS total_tokens
        FROM inference_logs
        WHERE created_at >= NOW() - INTERVAL '{sql_interval}' {where}
        GROUP BY provider, model
        ORDER BY cost_usd DESC
        """,
        *params,
    )
    return [dict(r) for r in rows]


async def get_traces(
    db,
    sql_interval: str,
    provider: str | None,
    model: str | None,
    status: str | None,
    limit: int,
    offset: int,
) -> list[dict]:
    where, params, idx = _filters(provider, model)
    if status:
        where += f" AND status = ${idx}"
        params.append(status)
        idx += 1
    limit_param, offset_param = idx, idx + 1
    params.extend([limit, offset])
    rows = await db.fetch(
        f"""
        SELECT id, request_id, trace_id, conversation_id, provider, model,
               status, latency_ms, time_to_first_token_ms,
               input_tokens, output_tokens, total_tokens, cost_usd,
               error_message, started_at, created_at
        FROM inference_logs
        WHERE created_at >= NOW() - INTERVAL '{sql_interval}' {where}
        ORDER BY created_at DESC
        LIMIT ${limit_param} OFFSET ${offset_param}
        """,
        *params,
    )
    return [dict(r) for r in rows]


async def get_trace_by_id(db, trace_id: UUID) -> list[dict]:
    rows = await db.fetch(
        """
        SELECT id, request_id, trace_id, conversation_id, provider, model,
               status, latency_ms, time_to_first_token_ms,
               input_tokens, output_tokens, total_tokens,
               cache_read_input_tokens, cache_creation_input_tokens, reasoning_tokens,
               cost_usd, error_message, input_preview, output_preview,
               raw_usage, attributes, started_at, created_at
        FROM inference_logs
        WHERE trace_id = $1
        ORDER BY created_at ASC
        """,
        trace_id,
    )
    return [dict(r) for r in rows]


async def get_conversation_stats(db, sql_interval: str, limit: int) -> list[dict]:
    rows = await db.fetch(
        f"""
        SELECT conversation_id,
               COUNT(*)                                                   AS requests,
               COUNT(*) FILTER (WHERE status = 'error')                   AS errors,
               COALESCE(SUM(cost_usd), 0)                                 AS total_cost_usd,
               COALESCE(AVG(latency_ms) FILTER (WHERE status = 'success'), 0) AS avg_latency_ms,
               COALESCE(SUM(total_tokens), 0)                             AS total_tokens,
               MAX(created_at)                                            AS last_activity
        FROM inference_logs
        WHERE conversation_id IS NOT NULL
          AND created_at >= NOW() - INTERVAL '{sql_interval}'
        GROUP BY conversation_id
        ORDER BY total_cost_usd DESC
        LIMIT $1
        """,
        limit,
    )
    return [dict(r) for r in rows]
