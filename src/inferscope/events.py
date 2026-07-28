"""Event-bus contract shared across the producer (SDK) and consumer (chatbot worker).

Pure module — defines the event shape, the Redis Stream constants, and the
``Publisher`` abstraction. It intentionally imports no Redis client, so the SDK
can depend on the abstraction without taking on an infrastructure dependency.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Stream wiring — one stream, one consumer group of ingestion workers.
STREAM_KEY = "lumino:inference:events"
CONSUMER_GROUP = "ingestion-workers"
EVENT_FIELD = "data"  # the single XADD field holding the JSON-encoded event


@dataclass
class InferenceEvent:
    """One inference log on its way to ingestion.

    ``payload`` is exactly the dict the wrapper builds today (the ingestion
    ``InferenceLogPayload`` shape) — one source of truth, no schema drift.
    ``request_id`` and ``trace_id`` are lifted out for routing and structured
    logging without re-parsing the payload.
    """

    payload: dict
    request_id: str
    trace_id: str | None = None

    @classmethod
    def from_log(cls, log: dict) -> InferenceEvent:
        return cls(
            payload=log,
            request_id=str(log.get("request_id")),
            trace_id=log.get("trace_id"),
        )

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict) -> InferenceEvent:
        return cls(
            payload=data["payload"],
            request_id=data["request_id"],
            trace_id=data.get("trace_id"),
        )

    def serialize(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def deserialize(cls, raw: str) -> InferenceEvent:
        return cls.from_dict(json.loads(raw))


@runtime_checkable
class Publisher(Protocol):
    """Abstraction the wrapper depends on (DIP). RedisProducer implements it."""

    async def publish(self, event: InferenceEvent) -> None: ...
