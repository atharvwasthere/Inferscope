"""Collector-side event bus: Redis Streams with a Postgres outbox fallback.

Both drains — the Redis consumer and the outbox poller — are parameterised by an
``EventHandler`` rather than an ingestion URL. They used to POST to the ingestion
service over HTTP because they ran in a different process (the chatbot); now that
they run inside the collector itself, the handler is a local persist call.

Keeping the outcome vocabulary (``DeliveryOutcome``) means the ack / drop / retry
logic in both drains is unchanged by that swap — only what "delivery" *means*
moved from a socket to a function call.
"""
from collections.abc import Awaitable, Callable

from inferscope.delivery import DeliveryOutcome
from inferscope.events import InferenceEvent

EventHandler = Callable[[InferenceEvent], Awaitable[DeliveryOutcome]]

__all__ = ["EventHandler"]
