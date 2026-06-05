"""Stream-abort signalling.

Two collaborators, one purpose: deliver an "abort stream X" signal from any
chatbot replica to the replica that actually owns the in-flight generator.

  - ``StreamRegistry`` is the per-process map of ``stream_id -> asyncio.Event``.
    The SSE generator registers an event, reads it between provider tokens, and
    breaks out of the loop when it flips. This is the only place that knows the
    generator exists.

  - ``AbortBus`` is the fan-out primitive. ``publish(stream_id)`` broadcasts to
    every replica; each replica's subscriber asks its local registry to flip
    the matching event (a no-op on replicas that don't own that stream).

The two are kept separate on purpose: a unit test for the registry needs no
Redis; a unit test for the bus needs no asyncio Event. The wiring (subscriber
calls ``registry.cancel``) lives in ``main.py``.

Why pub/sub over an in-process dict — see README "Stream-abort signal"
tradeoff. Short version: a local dict silently breaks the moment chatbot scales
to N>1 replicas because the /abort POST can land on any pod, but only the pod
owning the task can stop it.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Protocol
from uuid import UUID

import redis.asyncio as redis

from obs.log import get_logger, log_with

logger = get_logger("chatbot.abort_bus")

ABORT_CHANNEL = "inferscope:stream:abort"


class AbortBus(Protocol):
    """Fan-out signal for stream aborts. Implementations: Redis pub/sub (prod),
    in-memory (tests). The ChatService never depends on this directly — main.py
    wires the bus to the registry once at startup."""

    async def publish(self, stream_id: UUID) -> None: ...
    async def start(self, on_abort: Callable[[UUID], None]) -> None: ...
    async def close(self) -> None: ...


class StreamRegistry:
    """Per-process map: stream_id -> asyncio.Event.

    The SSE generator owns the lifecycle:
        event = registry.register(stream_id)
        try:
            ... # check event.is_set() between tokens
        finally:
            registry.unregister(stream_id)

    ``cancel`` is what the AbortBus subscriber calls when it receives a
    broadcast. Returns True if the stream was owned by this process — useful
    for metrics ("how often did the pub/sub message land on the right pod").
    """

    def __init__(self) -> None:
        self._events: dict[UUID, asyncio.Event] = {}

    def register(self, stream_id: UUID) -> asyncio.Event:
        event = asyncio.Event()
        self._events[stream_id] = event
        return event

    def unregister(self, stream_id: UUID) -> None:
        self._events.pop(stream_id, None)

    def cancel(self, stream_id: UUID) -> bool:
        event = self._events.get(stream_id)
        if event is None:
            return False
        event.set()
        return True

    def active_count(self) -> int:
        return len(self._events)


class RedisAbortBus:
    """Redis pub/sub implementation of AbortBus.

    Channel: a single shared channel; payload is the stream UUID. Every chatbot
    replica subscribes; the one that owns the task flips its local event, the
    rest no-op. Pub/sub is the right primitive here (fire-and-forget broadcast,
    no persistence needed) — Streams would be wrong because there is no "queue
    of unread aborts": if no replica owns the stream right now, the abort has
    nothing to cancel and should be dropped silently.
    """

    def __init__(self, redis_url: str, channel: str = ABORT_CHANNEL) -> None:
        self._redis = redis.from_url(redis_url, decode_responses=True)
        self._channel = channel
        self._pubsub: redis.client.PubSub | None = None
        self._task: asyncio.Task | None = None

    async def publish(self, stream_id: UUID) -> None:
        try:
            await self._redis.publish(self._channel, str(stream_id))
        except Exception as e:
            log_with(
                logger, logging.WARNING, "abort publish failed",
                stream_id=str(stream_id), error=f"{type(e).__name__}: {e}",
            )

    async def start(self, on_abort: Callable[[UUID], None]) -> None:
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(self._channel)
        self._task = asyncio.create_task(self._run(on_abort))

    async def _run(self, on_abort: Callable[[UUID], None]) -> None:
        assert self._pubsub is not None
        try:
            async for msg in self._pubsub.listen():
                if msg.get("type") != "message":
                    continue
                raw = msg.get("data")
                try:
                    stream_id = UUID(raw)
                except (ValueError, TypeError, AttributeError):
                    continue
                try:
                    on_abort(stream_id)
                except Exception as e:
                    log_with(
                        logger, logging.WARNING, "on_abort callback raised",
                        stream_id=str(stream_id), error=f"{type(e).__name__}: {e}",
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log_with(
                logger, logging.ERROR, "abort subscriber crashed",
                error=f"{type(e).__name__}: {e}",
            )

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._pubsub is not None:
            try:
                await self._pubsub.unsubscribe(self._channel)
                await self._pubsub.aclose()
            except Exception:
                pass
            self._pubsub = None
        await self._redis.aclose()
