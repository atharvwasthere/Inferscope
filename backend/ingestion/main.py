import logging
from contextlib import asynccontextmanager
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI

from ingestion.db import (
    close_pool,
    get_db,
    get_provider_rates,
    init_pool,
    insert_inference_log,
)
from ingestion.models import InferenceLogPayload
from ingestion.pricing import compute_cost
from obs.log import configure_logging, get_logger, log_with
from obs.middleware import TraceIdMiddleware
from sdk.redactor import redact

configure_logging("ingestion")
logger = get_logger("ingestion")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    yield
    await close_pool()


app = FastAPI(title="inferscope-ingestion", lifespan=lifespan)
app.add_middleware(TraceIdMiddleware)


def _enrich(payload: InferenceLogPayload) -> dict:
    row = payload.model_dump()
    row["input_preview"] = redact(payload.input_preview) if payload.input_preview else None
    row["output_preview"] = redact(payload.output_preview) if payload.output_preview else None

    if row.get("total_tokens") is None:
        row["total_tokens"] = (payload.input_tokens or 0) + (payload.output_tokens or 0)

    return row


async def _price(db: asyncpg.Connection, payload: InferenceLogPayload) -> float | None:
    if payload.cost_usd is not None:
        return payload.cost_usd
    if payload.status != "success":
        return None
    rates = await get_provider_rates(db, payload.provider, payload.model)
    return compute_cost(payload.input_tokens or 0, payload.output_tokens or 0, rates)


@app.get("/health")
async def health() -> dict:
    """Liveness — process is up."""
    return {"status": "ok"}


@app.get("/ready")
async def ready(db: asyncpg.Connection = Depends(get_db)) -> dict:
    """Readiness — DB reachable."""
    await db.fetchval("SELECT 1")
    return {"status": "ready"}


@app.post("/ingest")
async def ingest(
    payload: InferenceLogPayload,
    db: asyncpg.Connection = Depends(get_db),
) -> dict:
    row = _enrich(payload)
    row["cost_usd"] = await _price(db, payload)

    row_id: UUID | None = await insert_inference_log(db, row)

    log_with(
        logger,
        logging.INFO,
        "ingested" if row_id else "deduped",
        request_id=str(payload.request_id),
        provider=payload.provider,
        model=payload.model,
        status=payload.status,
        cost_usd=row["cost_usd"],
        deduped=row_id is None,
    )

    if row_id is None:
        return {"id": None, "deduped": True, "cost_usd": row["cost_usd"]}
    return {"id": str(row_id), "deduped": False, "cost_usd": row["cost_usd"]}
