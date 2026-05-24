"""Redis Streams consumer — drains the stream and delivers to ingestion.

Runs as a background task inside the chatbot lifespan. Uses a consumer group so
the work can be parallelised across replicas, and XAUTOCLAIM so messages stranded
by a crashed worker are reclaimed. Delivery itself lives in ``delivery.deliver``
(shared with the outbox poller).
"""
from __future__ import annotations

import asyncio
import logging
import socket

import httpx
import redis.asyncio as redis
from redis.exceptions import ResponseError

from obs.events import CONSUMER_GROUP, EVENT_FIELD, STREAM_KEY, InferenceEvent
from obs.log import get_logger, log_with
from obs.trace import set_trace_id
from redis_bus.delivery import DeliveryOutcome, deliver

logger = get_logger("redis_bus.consumer")

_RECLAIM_MIN_IDLE_MS = 30_000


class IngestionWorker:
    def __init__(
        self,
        redis_url: str,
        ingestion_url: str,
        group: str = CONSUMER_GROUP,
        consumer: str | None = None,
        stream_key: str = STREAM_KEY,
        batch: int = 10,
        block_ms: int = 2000,
    ) -> None:
        self._redis = redis.from_url(redis_url, decode_responses=True)
        self._client = httpx.AsyncClient(timeout=5.0)
        self._ingestion_url = ingestion_url
        self._group = group
        self._consumer = consumer or socket.gethostname()
        self._stream_key = stream_key
        self._batch = batch
        self._block_ms = block_ms

    async def run(self) -> None:
        await self._ensure_group()
        log_with(logger, logging.INFO, "ingestion worker started",
                 group=self._group, consumer=self._consumer)
        try:
            while True:
                await self._reclaim_stale()
                messages = await self._redis.xreadgroup(
                    self._group,
                    self._consumer,
                    {self._stream_key: ">"},
                    count=self._batch,
                    block=self._block_ms,
                )
                for _stream, entries in messages or []:
                    for msg_id, fields in entries:
                        await self._process(msg_id, fields)
        except asyncio.CancelledError:
            raise

    async def _ensure_group(self) -> None:
        try:
            await self._redis.xgroup_create(self._stream_key, self._group, id="0", mkstream=True)
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise  # group already exists is fine; anything else is real

    async def _reclaim_stale(self) -> None:
        """Reclaim messages a dead worker left pending, then process them."""
        _cursor, claimed, _deleted = await self._redis.xautoclaim(
            self._stream_key, self._group, self._consumer,
            min_idle_time=_RECLAIM_MIN_IDLE_MS, start_id="0-0", count=self._batch,
        )
        for msg_id, fields in claimed:
            await self._process(msg_id, fields)

    async def _process(self, msg_id: str, fields: dict) -> None:
        event = InferenceEvent.deserialize(fields[EVENT_FIELD])
        if event.trace_id:
            set_trace_id(event.trace_id)

        outcome = await deliver(self._client, self._ingestion_url, event)
        if outcome is DeliveryOutcome.DELIVERED:
            await self._redis.xack(self._stream_key, self._group, msg_id)
        elif outcome is DeliveryOutcome.REJECTED:
            log_with(logger, logging.WARNING, "ingestion rejected event (dropped)",
                     request_id=event.request_id)
            await self._redis.xack(self._stream_key, self._group, msg_id)
        else:  # FAILED — leave unacked, redelivered on a later read / reclaim
            log_with(logger, logging.WARNING, "ingest delivery failed, will redeliver",
                     request_id=event.request_id)

    async def close(self) -> None:
        await self._client.aclose()
        await self._redis.aclose()
