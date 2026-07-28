"""Delivery of an event to the collector.

One HTTP path shared by three callers: the SDK's ``HttpPublisher``, the Redis
consumer, and the outbox poller. Owns the POST, the retry policy, and the
classification of the response into an outcome the caller acts on.

Retry is hand-rolled rather than delegated to tenacity: this module ships inside
an installable package, and every declared dependency is one the SDK forces on
whoever installs it. Ten lines of backoff is a better trade than a second
transitive dep — ``httpx`` is the only dependency inferscope declares.
"""
from __future__ import annotations

import asyncio
import random
from enum import Enum

import httpx

from inferscope.events import InferenceEvent
from inferscope.trace import TRACE_HEADER


class DeliveryOutcome(Enum):
    DELIVERED = "delivered"   # 2xx — ack / mark processed
    REJECTED = "rejected"     # permanent client error (422/4xx) — drop, do not retry
    FAILED = "failed"         # transient (5xx / network) — leave for redelivery


class _Transient(Exception):
    """Raised for retryable responses so the backoff loop retries them."""


_MAX_ATTEMPTS = 3
_INITIAL_BACKOFF = 0.1
_MAX_BACKOFF = 2.0


async def _post(
    client: httpx.AsyncClient, url: str, payload: dict, headers: dict
) -> httpx.Response:
    """POST with bounded exponential backoff. Raises the last error if every attempt fails.

    Full jitter (sleep uniformly in ``[0, backoff]``) rather than fixed backoff — a
    collector recovering from an outage gets retries spread out instead of every
    client in the fleet retrying on the same tick.
    """
    backoff = _INITIAL_BACKOFF
    last_error: Exception = _Transient("no attempt made")

    for attempt in range(_MAX_ATTEMPTS):
        try:
            response = await client.post(url, json=payload, headers=headers)
            # 4xx is permanent — hand it back so deliver() classifies it REJECTED.
            if response.status_code < 500:
                return response
            last_error = _Transient(f"collector returned {response.status_code}")
        except httpx.HTTPError as e:
            last_error = e

        if attempt < _MAX_ATTEMPTS - 1:
            await asyncio.sleep(random.uniform(0, backoff))
            backoff = min(backoff * 2, _MAX_BACKOFF)

    raise last_error


async def deliver(
    client: httpx.AsyncClient,
    ingestion_url: str,
    event: InferenceEvent,
    headers: dict | None = None,
) -> DeliveryOutcome:
    """POST one event to the collector and classify the result. Never raises."""
    request_headers = dict(headers) if headers else {}
    if event.trace_id:
        request_headers[TRACE_HEADER] = event.trace_id

    try:
        response = await _post(client, ingestion_url, event.payload, request_headers)
    except Exception:
        # network error or 5xx after all retries — transient, redeliver later
        return DeliveryOutcome.FAILED

    if response.is_success:
        return DeliveryOutcome.DELIVERED
    # any non-5xx, non-2xx (422 validation, other 4xx) is a permanent rejection
    return DeliveryOutcome.REJECTED
