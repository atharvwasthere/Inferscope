"""Outbox poller — drains the Postgres fallback table to ingestion.

Runs as a background task inside the chatbot lifespan. Every ``interval_s`` it
pulls a batch of unprocessed rows and delivers them through the same
``delivery.deliver`` path the Redis consumer uses, so there is no duplicated
delivery logic. Idempotency at ingestion makes a double-delivery (Redis recovered
*and* outbox fired) harmless.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

import asyncpg
import httpx

from obs.log import get_logger, log_with
from obs.trace import set_trace_id
from redis_bus import outbox
from redis_bus.delivery import DeliveryOutcome, deliver

logger = get_logger("redis_bus.outbox_poller")

PoolGetter = Callable[[], "asyncpg.Pool | None"]


class OutboxPoller:
    def __init__(
        self,
        pool_getter: PoolGetter,
        ingestion_url: str,
        interval_s: int = 10,
        batch: int = 50,
    ) -> None:
        self._pool_getter = pool_getter
        self._ingestion_url = ingestion_url
        self._interval_s = interval_s
        self._batch = batch
        self._client = httpx.AsyncClient(timeout=5.0)

    async def run(self) -> None:
        log_with(logger, logging.INFO, "outbox poller started", interval_s=self._interval_s)
        try:
            while True:
                await self._drain_once()
                await asyncio.sleep(self._interval_s)
        except asyncio.CancelledError:
            raise

    async def _drain_once(self) -> None:
        pool = self._pool_getter()
        if pool is None:
            return
        rows = await outbox.fetch_unprocessed(pool, limit=self._batch)
        for outbox_id, event in rows:
            if event.trace_id:
                set_trace_id(event.trace_id)
            outcome = await deliver(self._client, self._ingestion_url, event)
            if outcome is DeliveryOutcome.DELIVERED:
                await outbox.mark_processed(pool, outbox_id)
            elif outcome is DeliveryOutcome.REJECTED:
                log_with(logger, logging.WARNING, "ingestion rejected outbox event (dropped)",
                         request_id=event.request_id)
                await outbox.mark_processed(pool, outbox_id)
            else:  # FAILED — leave processed=FALSE, retry next cycle
                log_with(logger, logging.WARNING, "outbox delivery failed, will retry",
                         request_id=event.request_id)

    async def close(self) -> None:
        await self._client.aclose()
