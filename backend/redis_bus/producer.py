"""Redis Streams producer with a Postgres outbox fallback.

Implements the ``Publisher`` abstraction the wrapper depends on. The wrapper
calls ``publish()`` and knows nothing about Redis or the outbox — this class is
the only place that knows the fallback exists.

Dual path:
  1. happy path  — XADD to the stream.
  2. fallback    — on ANY Redis error, INSERT into ``outbox_events`` so the
     event survives until the poller delivers it.

``publish()`` never raises: log delivery is best-effort and must never break the
chat path.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

import asyncpg
import redis.asyncio as redis

from inferscope.events import EVENT_FIELD, STREAM_KEY, InferenceEvent, Publisher
from obs.log import get_logger, log_with
from redis_bus import outbox

logger = get_logger("redis_bus.producer")

PoolGetter = Callable[[], "asyncpg.Pool | None"]


class RedisProducer(Publisher):
    def __init__(
        self,
        redis_url: str,
        pool_getter: PoolGetter | None = None,
        stream_key: str = STREAM_KEY,
        maxlen: int = 10_000,
    ) -> None:
        # from_url is lazy — no connection until the first command.
        self._redis = redis.from_url(redis_url, decode_responses=True)
        self._pool_getter = pool_getter
        self._stream_key = stream_key
        self._maxlen = maxlen

    async def publish(self, event: InferenceEvent) -> None:
        try:
            await self._redis.xadd(
                self._stream_key,
                {EVENT_FIELD: event.serialize()},
                maxlen=self._maxlen,
                approximate=True,
            )
        except Exception as e:
            await self._fallback(event, e)

    async def _fallback(self, event: InferenceEvent, cause: Exception) -> None:
        pool = self._pool_getter() if self._pool_getter else None
        if pool is None:
            log_with(
                logger, logging.ERROR, "event dropped: redis down and no outbox pool",
                request_id=event.request_id, error=f"{type(cause).__name__}: {cause}",
            )
            return
        try:
            await outbox.insert(pool, event)
            log_with(
                logger, logging.WARNING, "redis publish failed, wrote outbox",
                request_id=event.request_id, error=f"{type(cause).__name__}: {cause}",
            )
        except Exception as oe:
            log_with(
                logger, logging.ERROR, "event dropped: redis down and outbox write failed",
                request_id=event.request_id, error=f"{type(oe).__name__}: {oe}",
            )

    async def close(self) -> None:
        await self._redis.aclose()
