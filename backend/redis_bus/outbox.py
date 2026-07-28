"""Outbox repository — the only place that touches the ``outbox_events`` table.

The outbox is the durable fallback path: when Redis is unreachable the producer
writes here, and the poller drains it once services recover. It is staging, not
storage.

NOTE: cleanup of processed rows older than 24h is out of scope here — in
production a periodic job (cron / pg_cron) deletes ``processed = TRUE`` rows.
"""
from __future__ import annotations

import json
from uuid import UUID

import asyncpg

from inferscope.events import InferenceEvent


async def insert(pool: asyncpg.Pool, event: InferenceEvent) -> None:
    await pool.execute(
        """
        INSERT INTO outbox_events (request_id, trace_id, payload)
        VALUES ($1, $2, $3)
        """,
        event.request_id,
        event.trace_id,
        json.dumps(event.payload),
    )


async def fetch_unprocessed(
    pool: asyncpg.Pool, limit: int = 50
) -> list[tuple[UUID, InferenceEvent]]:
    rows = await pool.fetch(
        """
        SELECT id, request_id, trace_id, payload
        FROM outbox_events
        WHERE processed = FALSE
        ORDER BY created_at ASC
        LIMIT $1
        """,
        limit,
    )
    return [
        (
            r["id"],
            InferenceEvent(
                payload=json.loads(r["payload"]),
                request_id=r["request_id"],
                trace_id=r["trace_id"],
            ),
        )
        for r in rows
    ]


async def mark_processed(pool: asyncpg.Pool, outbox_id: UUID) -> None:
    await pool.execute(
        "UPDATE outbox_events SET processed = TRUE, processed_at = NOW() WHERE id = $1",
        outbox_id,
    )
