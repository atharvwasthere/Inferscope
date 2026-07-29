"""The collector after the relocation (D-014): /ingest enqueues, the drains persist.

The route no longer writes to Postgres, so what matters is that it validates at
the boundary and hands a JSON-safe event to the producer. persist_event is the
other half — it must classify rather than raise, because the drains' ack / drop /
retry logic is driven entirely by the outcome it returns.
"""
from uuid import uuid4

import pytest
from pydantic import ValidationError

from inferscope.delivery import DeliveryOutcome
from inferscope.events import InferenceEvent
from ingestion import main as ingestion_main
from ingestion.models import InferenceLogPayload
from ingestion.persist import persist_event


def _payload(**overrides) -> InferenceLogPayload:
    data = {
        "request_id": uuid4(),
        "provider": "groq",
        "model": "llama-3.1-8b-instant",
        "status": "success",
        "input_tokens": 10,
        "output_tokens": 20,
    }
    data.update(overrides)
    return InferenceLogPayload(**data)


class _CapturingProducer:
    def __init__(self):
        self.published: list[InferenceEvent] = []

    async def publish(self, event: InferenceEvent) -> None:
        self.published.append(event)


class _FakeAcquire:
    def __init__(self, conn, error):
        self._conn, self._error = conn, error

    async def __aenter__(self):
        if self._error:
            raise self._error
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    """Minimal asyncpg.Pool stand-in — persist_event only ever calls acquire()."""

    def __init__(self, conn=None, error=None):
        self._conn, self._error = conn, error

    def acquire(self):
        return _FakeAcquire(self._conn, self._error)


# --- the route: validate and enqueue, do not persist ---------------------------


@pytest.mark.asyncio
async def test_ingest_enqueues_and_reports_queued(monkeypatch):
    producer = _CapturingProducer()
    monkeypatch.setattr(ingestion_main, "producer", producer)

    payload = _payload()
    result = await ingestion_main.ingest(payload)

    assert result["status"] == "queued"       # 202 semantics: not yet stored
    assert result["request_id"] == str(payload.request_id)
    assert len(producer.published) == 1


@pytest.mark.asyncio
async def test_enqueued_event_survives_a_json_round_trip(monkeypatch):
    """The payload goes through Redis as JSON, so UUIDs/datetimes must be strings."""
    producer = _CapturingProducer()
    monkeypatch.setattr(ingestion_main, "producer", producer)

    await ingestion_main.ingest(_payload())
    event = producer.published[0]

    # serialize() would raise on a raw UUID/datetime; this is the actual failure mode.
    restored = InferenceEvent.deserialize(event.serialize())
    assert restored.request_id == event.request_id
    assert isinstance(restored.payload["request_id"], str)


def test_invalid_payload_is_rejected_at_the_boundary():
    """Bad input fails while the caller is listening, not silently in a drain."""
    with pytest.raises(ValidationError):
        _payload(provider="not-a-real-provider")


# --- persist: classify, never raise --------------------------------------------


@pytest.mark.asyncio
async def test_persist_rejects_a_malformed_event():
    event = InferenceEvent(payload={"nonsense": True}, request_id=str(uuid4()))
    outcome = await persist_event(_FakePool(), event)
    # permanent — a retry would fail identically, so the drain should ack and drop
    assert outcome is DeliveryOutcome.REJECTED


@pytest.mark.asyncio
async def test_persist_reports_failed_when_the_database_is_unreachable():
    payload = _payload()
    event = InferenceEvent.from_log(payload.model_dump(mode="json"))

    outcome = await persist_event(_FakePool(error=OSError("no route to host")), event)

    # transient — must NOT be acked, so the drain redelivers it
    assert outcome is DeliveryOutcome.FAILED


@pytest.mark.asyncio
async def test_persist_never_raises_on_an_unexpected_error():
    payload = _payload()
    event = InferenceEvent.from_log(payload.model_dump(mode="json"))

    outcome = await persist_event(_FakePool(error=RuntimeError("boom")), event)

    assert outcome is DeliveryOutcome.FAILED  # classified, not propagated
