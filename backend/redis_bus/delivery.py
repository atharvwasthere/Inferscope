"""Delivery of an event to the ingestion service.

Shared by the Redis consumer and the outbox poller so both reach ingestion
through one code path (DRY). Owns the HTTP POST, the retry policy, and the
classification of the response into an outcome the caller acts on.
"""
from __future__ import annotations

from enum import Enum

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from obs.events import InferenceEvent
from obs.trace import TRACE_HEADER


class DeliveryOutcome(Enum):
    DELIVERED = "delivered"   # 2xx — ack / mark processed
    REJECTED = "rejected"     # permanent client error (422/4xx) — drop, do not retry
    FAILED = "failed"         # transient (5xx / network) — leave for redelivery


class _Transient(Exception):
    """Raised for retryable responses so tenacity retries them."""


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=0.1, max=2.0),
    retry=retry_if_exception_type((httpx.HTTPError, _Transient)),
)
async def _post(client: httpx.AsyncClient, url: str, payload: dict, headers: dict) -> httpx.Response:
    response = await client.post(url, json=payload, headers=headers)
    if response.status_code >= 500:
        raise _Transient(f"ingestion returned {response.status_code}")
    return response


async def deliver(client: httpx.AsyncClient, ingestion_url: str, event: InferenceEvent) -> DeliveryOutcome:
    """POST one event to ingestion and classify the result. Never raises."""
    headers = {TRACE_HEADER: event.trace_id} if event.trace_id else {}
    try:
        response = await _post(client, ingestion_url, event.payload, headers)
    except Exception:
        # network error or 5xx after all retries — transient, redeliver later
        return DeliveryOutcome.FAILED

    if response.is_success:
        return DeliveryOutcome.DELIVERED
    # any non-5xx, non-2xx (422 validation, other 4xx) is a permanent rejection
    return DeliveryOutcome.REJECTED
