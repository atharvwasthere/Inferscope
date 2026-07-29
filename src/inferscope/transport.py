"""HTTP transport — the ``Publisher`` implementation the SDK ships telemetry through.

A second implementation of the existing ``Publisher`` port, alongside the
collector-side ``RedisProducer``. The wrapper depends on the Protocol and knows
nothing about either, so swapping transports needs no change to calling code.

Shape:
  ``publish()`` enqueues and returns. It never blocks the caller and never
  raises — telemetry is best-effort and must not break the request path that
  produced it. A background task drains the queue and POSTs through
  ``delivery.deliver`` (the same path the collector's own drains use).

  When the queue is full, events are DROPPED rather than awaited. Shedding
  telemetry is strictly better than applying backpressure to a user's inference
  call; the drop is counted and logged so it is visible rather than silent.

Batching here means pipelining N concurrent POSTs over one connection pool, not
N events per request — the collector's ``/ingest`` accepts a single payload. A
true batch endpoint is a collector-side change and is deliberately not in scope.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from urllib.parse import urlparse

import httpx

from inferscope.delivery import DeliveryOutcome, deliver
from inferscope.events import InferenceEvent, Publisher

logger = logging.getLogger("inferscope.transport")

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

DEFAULT_QUEUE_SIZE = 1000
DEFAULT_CONCURRENCY = 8
DEFAULT_TIMEOUT = 5.0


def _is_local(url: str) -> bool:
    return (urlparse(url).hostname or "") in _LOCAL_HOSTS


class HttpPublisher(Publisher):
    """Ships inference events to a collector over HTTP.

    ``base_url`` is required and has no default. Per D-015 inferscope is
    self-host-first: there is no hosted endpoint to fall back to, so a missing
    URL is a configuration error worth failing loudly on rather than silently
    dropping telemetry into the void.

    ``api_key`` may be omitted for a local collector. Pointing at a remote host
    without one is refused — that combination is almost always a mistake, and
    the failure would otherwise surface as 401s buried in a background task.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        *,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        concurrency: int = DEFAULT_CONCURRENCY,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url:
            raise ValueError(
                "inferscope: base_url is required (e.g. http://localhost:8081). "
                "There is no hosted default — inferscope is self-hosted."
            )
        if api_key is None and not _is_local(base_url):
            raise ValueError(
                f"inferscope: api_key is required for a non-local collector ({base_url}). "
                "Omit it only when pointing at localhost."
            )

        self._url = base_url.rstrip("/") + "/ingest"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._concurrency = concurrency
        # Injectable so tests can drive httpx.MockTransport instead of a live server.
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._queue: asyncio.Queue[InferenceEvent] = asyncio.Queue(maxsize=queue_size)
        self._worker: asyncio.Task | None = None
        self._dropped = 0

    @property
    def dropped(self) -> int:
        """Events discarded because the queue was full. Non-zero means under-provisioned."""
        return self._dropped

    async def publish(self, event: InferenceEvent) -> None:
        """Enqueue an event. Never blocks, never raises (the Publisher contract)."""
        self._ensure_worker()
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._dropped += 1
            logger.warning(
                "inferscope: telemetry queue full, dropped event %s (%d dropped total)",
                event.request_id,
                self._dropped,
            )

    async def flush(self) -> None:
        """Wait until everything queued has been attempted. For shutdown and tests."""
        if self._worker is not None:
            await self._queue.join()

    async def aclose(self) -> None:
        """Flush, stop the drain, close the connection pool."""
        await self.flush()
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None
        await self._client.aclose()

    def _ensure_worker(self) -> None:
        """Start the drain lazily, so constructing this does not require a running loop."""
        if self._worker is None or self._worker.done():
            # Held on self — a bare create_task() is only weakly referenced by the
            # loop and can be garbage collected mid-flight (the bug in wrapper.py).
            self._worker = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        while True:
            batch = [await self._queue.get()]
            # Opportunistically take whatever else is already waiting, up to the
            # concurrency cap, so a burst costs one round trip's latency, not N.
            while len(batch) < self._concurrency and not self._queue.empty():
                batch.append(self._queue.get_nowait())

            try:
                results = await asyncio.gather(
                    *(self._deliver_one(event) for event in batch),
                    return_exceptions=True,
                )
                for event, result in zip(batch, results, strict=True):
                    if isinstance(result, BaseException):
                        logger.warning(
                            "inferscope: delivery raised for %s: %s",
                            event.request_id,
                            result,
                        )
            finally:
                for _ in batch:
                    self._queue.task_done()

    async def _deliver_one(self, event: InferenceEvent) -> DeliveryOutcome:
        outcome = await deliver(self._client, self._url, event, headers=self._headers)
        if outcome is not DeliveryOutcome.DELIVERED:
            logger.warning(
                "inferscope: event %s %s", event.request_id, outcome.value
            )
        return outcome
