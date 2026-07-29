"""Does the collector's 202 actually mean anything?

T-relocate flipped `/ingest` from "persisted" (200) to "queued" (202). That is only
an honest answer if the event survives the gap between accept and persist. These
tests exercise the two survival paths with fakes:

  Redis up   — event lands on the stream, the drain persists it later
  Redis down — producer falls back to the outbox table, the poller persists it later

LIMITATION, stated plainly: these prove OUR logic (fallback selection, the drain's
ack/retry semantics, the outbox round trip). They do NOT prove durability against a
real Redis or a real Postgres — that needs testcontainers and is not yet written.
A green run here does not license the claim "the 202 is durable in production".
"""
from uuid import uuid4

import pytest

from inferscope.delivery import DeliveryOutcome
from inferscope.events import InferenceEvent
from redis_bus.outbox_poller import OutboxPoller
from redis_bus.producer import RedisProducer


def _event() -> InferenceEvent:
    request_id = str(uuid4())
    return InferenceEvent(
        payload={"request_id": request_id, "provider": "groq", "status": "success"},
        request_id=request_id,
    )


class _DeadRedis:
    async def xadd(self, *a, **kw):
        raise ConnectionError("redis unreachable")

    async def aclose(self):
        pass


class _LiveRedis:
    def __init__(self):
        self.entries = []

    async def xadd(self, key, fields, **kw):
        self.entries.append((key, fields))

    async def aclose(self):
        pass


class _OutboxPool:
    """Records outbox writes and replays them, standing in for Postgres."""

    def __init__(self):
        self.rows: list[tuple] = []
        self.processed: list = []

    async def execute(self, sql: str, *args):
        if "INSERT INTO outbox_events" in sql:
            self.rows.append(args)
        elif "UPDATE outbox_events" in sql:
            self.processed.append(args[0])

    async def fetch(self, sql: str, *args):
        return [
            {"id": uuid4(), "request_id": r[0], "trace_id": r[1], "payload": r[2]}
            for r in self.rows
        ]


@pytest.mark.asyncio
async def test_redis_up_the_event_reaches_the_stream():
    producer = RedisProducer("redis://unused")
    producer._redis = _LiveRedis()

    event = _event()
    await producer.publish(event)

    assert len(producer._redis.entries) == 1  # queued, awaiting the drain


@pytest.mark.asyncio
async def test_redis_down_the_event_falls_back_to_the_outbox():
    """The accept must not be lost just because the stream is unavailable."""
    pool = _OutboxPool()
    producer = RedisProducer("redis://unused", pool_getter=lambda: pool)
    producer._redis = _DeadRedis()

    await producer.publish(_event())

    assert len(pool.rows) == 1  # durable, not dropped


@pytest.mark.asyncio
async def test_publish_never_raises_even_with_redis_down_and_no_outbox():
    """Worst case — both paths gone. Still must not break the caller."""
    producer = RedisProducer("redis://unused", pool_getter=lambda: None)
    producer._redis = _DeadRedis()

    await producer.publish(_event())  # logged and dropped, not raised


@pytest.mark.asyncio
async def test_an_event_accepted_during_an_outage_is_persisted_once_the_drain_resumes():
    """The durability claim behind the 202, end to end through our own code.

    Accept while Redis is down (event -> outbox), then let the poller run as if
    the drain had been paused and resumed. The event must reach the handler.
    """
    pool = _OutboxPool()
    producer = RedisProducer("redis://unused", pool_getter=lambda: pool)
    producer._redis = _DeadRedis()

    event = _event()
    await producer.publish(event)          # 202 returned to the SDK here
    assert pool.rows, "accept was not made durable"

    persisted: list[InferenceEvent] = []

    async def handler(e: InferenceEvent) -> DeliveryOutcome:
        persisted.append(e)
        return DeliveryOutcome.DELIVERED

    poller = OutboxPoller(lambda: pool, handler)
    await poller._drain_once()             # the drain comes back up

    assert [e.request_id for e in persisted] == [event.request_id]
    assert len(pool.processed) == 1        # and the outbox row is retired


@pytest.mark.asyncio
async def test_a_failed_persist_leaves_the_outbox_row_for_retry():
    """FAILED must not retire the row, or the 202 becomes a lie on the next crash."""
    pool = _OutboxPool()
    producer = RedisProducer("redis://unused", pool_getter=lambda: pool)
    producer._redis = _DeadRedis()
    await producer.publish(_event())

    async def failing_handler(e: InferenceEvent) -> DeliveryOutcome:
        return DeliveryOutcome.FAILED

    poller = OutboxPoller(lambda: pool, failing_handler)
    await poller._drain_once()

    assert pool.processed == []  # still pending, will be retried next cycle
