"""Persisting a drained event to Postgres — the collector's terminal step.

This is the ``EventHandler`` both drains call. It used to be the body of the
``POST /ingest`` route, reached over HTTP from the chatbot process; now that the
drains run inside the collector it is a local call, and the route's job shrank to
validate-and-enqueue.

Returns a ``DeliveryOutcome`` rather than raising so the drains keep their existing
ack / drop / retry semantics unchanged:
  DELIVERED — persisted (or deduped, which is equally final)
  REJECTED  — the payload will never be valid; ack it and drop
  FAILED    — the database was unreachable; leave it for redelivery
"""
from __future__ import annotations

import logging

import asyncpg
from pydantic import ValidationError

from inferscope.delivery import DeliveryOutcome
from inferscope.events import InferenceEvent
from inferscope.redactor import redact
from ingestion.db import get_provider_rates, insert_inference_log
from ingestion.models import InferenceLogPayload
from ingestion.pricing import compute_cost
from obs.log import get_logger, log_with

logger = get_logger("ingestion.persist")


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


async def persist_event(pool: asyncpg.Pool, event: InferenceEvent) -> DeliveryOutcome:
    """Validate, enrich, price and persist one event. Never raises."""
    try:
        payload = InferenceLogPayload.model_validate(event.payload)
    except ValidationError as e:
        # A malformed payload will never become valid on a retry.
        log_with(
            logger, logging.WARNING, "rejected invalid event",
            request_id=event.request_id, error=str(e)[:300],
        )
        return DeliveryOutcome.REJECTED

    try:
        async with pool.acquire() as conn:
            row = _enrich(payload)
            row["cost_usd"] = await _price(conn, payload)
            row_id = await insert_inference_log(conn, row)
    except Exception as e:
        log_with(
            logger, logging.WARNING, "persist failed, will redeliver",
            request_id=event.request_id, error=f"{type(e).__name__}: {e}",
        )
        return DeliveryOutcome.FAILED

    log_with(
        logger, logging.INFO, "persisted" if row_id else "deduped",
        request_id=str(payload.request_id), provider=payload.provider,
        model=payload.model, status=payload.status,
        cost_usd=row["cost_usd"], deduped=row_id is None,
    )
    return DeliveryOutcome.DELIVERED
