import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from dashboard import db as repo
from dashboard.db import close_pool, get_db, init_pool
from obs.log import configure_logging
from obs.middleware import TraceIdMiddleware

configure_logging("dashboard")

CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")


# interval window → (SQL interval string, date_trunc bucket unit)
_INTERVALS: dict[str, tuple[str, str]] = {
    "1h": ("1 hour", "minute"),
    "6h": ("6 hours", "minute"),
    "24h": ("24 hours", "hour"),
    "7d": ("7 days", "hour"),
}


def interval_to_sql(interval: str) -> tuple[str, str]:
    """Whitelist-validate the interval and map it to (sql_interval, bucket).

    Raises 400 on anything outside the four allowed windows, so the value
    interpolated into the query string is never user-controlled.
    """
    if interval not in _INTERVALS:
        raise HTTPException(status_code=400, detail=f"interval must be one of {sorted(_INTERVALS)}")
    return _INTERVALS[interval]


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    yield
    await close_pool()


app = FastAPI(title="inferscope-dashboard", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Trace-Id"],
)
app.add_middleware(TraceIdMiddleware)


# ---- serialization helpers ----------------------------------------------

def _f(x) -> float | None:
    return float(x) if isinstance(x, Decimal) else x


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _s(u) -> str | None:
    return str(u) if u is not None else None


def _json(v):
    """asyncpg returns JSONB columns as text — parse to objects for the API."""
    if isinstance(v, str):
        return json.loads(v)
    return v


# ---- health ---------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    """Liveness — process is up."""
    return {"status": "ok"}


@app.get("/ready")
async def ready(db: asyncpg.Connection = Depends(get_db)) -> dict:
    """Readiness — DB reachable."""
    await db.fetchval("SELECT 1")
    return {"status": "ready"}


# ---- metrics --------------------------------------------------------------

@app.get("/dashboard/summary")
async def summary(
    interval: str = Query("24h"),
    provider: str | None = None,
    model: str | None = None,
    db: asyncpg.Connection = Depends(get_db),
) -> dict:
    sql_interval, _ = interval_to_sql(interval)
    row = await repo.get_summary(db, sql_interval, provider, model)
    total = row["total_requests"] or 0
    errors = row["errors"] or 0
    return {
        "total_requests": total,
        "errors": errors,
        "error_rate_pct": round(errors / total * 100, 2) if total else 0.0,
        "total_cost_usd": _f(row["total_cost_usd"]),
        "avg_latency_ms": _f(row["avg_latency_ms"]),
        "total_tokens": row["total_tokens"],
    }


@app.get("/dashboard/latency")
async def latency(
    interval: str = Query("1h"),
    provider: str | None = None,
    model: str | None = None,
    db: asyncpg.Connection = Depends(get_db),
) -> dict:
    sql_interval, _ = interval_to_sql(interval)
    row = await repo.get_latency(db, sql_interval, provider, model)
    return {
        "p50_ms": _f(row["p50_ms"]),
        "p95_ms": _f(row["p95_ms"]),
        "avg_ms": _f(row["avg_ms"]),
        "avg_ttft_ms": _f(row["avg_ttft_ms"]),
    }


@app.get("/dashboard/throughput")
async def throughput(
    interval: str = Query("1h"),
    provider: str | None = None,
    model: str | None = None,
    db: asyncpg.Connection = Depends(get_db),
) -> list[dict]:
    sql_interval, bucket = interval_to_sql(interval)
    rows = await repo.get_throughput(db, sql_interval, bucket, provider, model)
    return [{"bucket": _iso(r["bucket"]), "count": r["count"]} for r in rows]


@app.get("/dashboard/latency/series")
async def latency_series(
    interval: str = Query("1h"),
    provider: str | None = None,
    model: str | None = None,
    db: asyncpg.Connection = Depends(get_db),
) -> list[dict]:
    sql_interval, bucket = interval_to_sql(interval)
    rows = await repo.get_latency_series(db, sql_interval, bucket, provider, model)
    return [
        {"bucket": _iso(r["bucket"]), "p50_ms": _f(r["p50_ms"]), "p95_ms": _f(r["p95_ms"])}
        for r in rows
    ]


@app.get("/dashboard/errors/series")
async def errors_series(
    interval: str = Query("1h"),
    provider: str | None = None,
    model: str | None = None,
    db: asyncpg.Connection = Depends(get_db),
) -> list[dict]:
    sql_interval, bucket = interval_to_sql(interval)
    rows = await repo.get_error_series(db, sql_interval, bucket, provider, model)
    return [
        {
            "bucket": _iso(r["bucket"]),
            "total": r["total"],
            "errors": r["errors"],
            "error_rate_pct": _f(r["error_rate_pct"]) or 0.0,
        }
        for r in rows
    ]


@app.get("/dashboard/errors")
async def errors(
    interval: str = Query("1h"),
    provider: str | None = None,
    model: str | None = None,
    db: asyncpg.Connection = Depends(get_db),
) -> dict:
    sql_interval, _ = interval_to_sql(interval)
    row = await repo.get_errors(db, sql_interval, provider, model)
    total = row["total"] or 0
    errs = row["errors"] or 0
    return {
        "total": total,
        "errors": errs,
        "error_rate_pct": round(errs / total * 100, 2) if total else 0.0,
    }


@app.get("/dashboard/cost")
async def cost(
    interval: str = Query("24h"),
    provider: str | None = None,
    model: str | None = None,
    db: asyncpg.Connection = Depends(get_db),
) -> list[dict]:
    sql_interval, _ = interval_to_sql(interval)
    rows = await repo.get_cost(db, sql_interval, provider, model)
    return [
        {
            "provider": r["provider"],
            "model": r["model"],
            "cost_usd": _f(r["cost_usd"]),
            "requests": r["requests"],
            "total_tokens": r["total_tokens"],
        }
        for r in rows
    ]


# ---- traces ---------------------------------------------------------------

@app.get("/dashboard/traces")
async def traces(
    interval: str = Query("24h"),
    provider: str | None = None,
    model: str | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: asyncpg.Connection = Depends(get_db),
) -> list[dict]:
    sql_interval, _ = interval_to_sql(interval)
    if status is not None and status not in {"success", "error"}:
        raise HTTPException(status_code=400, detail="status must be 'success' or 'error'")
    rows = await repo.get_traces(db, sql_interval, provider, model, status, limit, offset)
    return [
        {
            "id": _s(r["id"]),
            "request_id": _s(r["request_id"]),
            "trace_id": _s(r["trace_id"]),
            "conversation_id": _s(r["conversation_id"]),
            "provider": r["provider"],
            "model": r["model"],
            "status": r["status"],
            "latency_ms": _f(r["latency_ms"]),
            "time_to_first_token_ms": _f(r["time_to_first_token_ms"]),
            "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"],
            "total_tokens": r["total_tokens"],
            "cost_usd": _f(r["cost_usd"]),
            "error_message": r["error_message"],
            "started_at": _iso(r["started_at"]),
            "created_at": _iso(r["created_at"]),
        }
        for r in rows
    ]


@app.get("/dashboard/traces/{trace_id}")
async def trace_detail(
    trace_id: UUID,
    db: asyncpg.Connection = Depends(get_db),
) -> list[dict]:
    rows = await repo.get_trace_by_id(db, trace_id)
    if not rows:
        raise HTTPException(status_code=404, detail="trace not found")
    return [
        {
            "id": _s(r["id"]),
            "request_id": _s(r["request_id"]),
            "trace_id": _s(r["trace_id"]),
            "conversation_id": _s(r["conversation_id"]),
            "provider": r["provider"],
            "model": r["model"],
            "status": r["status"],
            "latency_ms": _f(r["latency_ms"]),
            "time_to_first_token_ms": _f(r["time_to_first_token_ms"]),
            "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"],
            "total_tokens": r["total_tokens"],
            "cache_read_input_tokens": r["cache_read_input_tokens"],
            "cache_creation_input_tokens": r["cache_creation_input_tokens"],
            "reasoning_tokens": r["reasoning_tokens"],
            "cost_usd": _f(r["cost_usd"]),
            "error_message": r["error_message"],
            "input_preview": r["input_preview"],
            "output_preview": r["output_preview"],
            "raw_usage": _json(r["raw_usage"]),
            "attributes": _json(r["attributes"]),
            "started_at": _iso(r["started_at"]),
            "created_at": _iso(r["created_at"]),
        }
        for r in rows
    ]


# ---- conversation-level aggregates ----------------------------------------

@app.get("/dashboard/conversations/stats")
async def conversation_stats(
    interval: str = Query("24h"),
    limit: int = Query(50, ge=1, le=500),
    db: asyncpg.Connection = Depends(get_db),
) -> list[dict]:
    sql_interval, _ = interval_to_sql(interval)
    rows = await repo.get_conversation_stats(db, sql_interval, limit)
    return [
        {
            "conversation_id": _s(r["conversation_id"]),
            "requests": r["requests"],
            "errors": r["errors"],
            "total_cost_usd": _f(r["total_cost_usd"]),
            "avg_latency_ms": _f(r["avg_latency_ms"]),
            "total_tokens": r["total_tokens"],
            "last_activity": _iso(r["last_activity"]),
        }
        for r in rows
    ]
