"""HttpPublisher — the Publisher contract, and the config rules from D-015.

Driven through httpx.MockTransport, so these are real requests through the real
delivery path with no server. What is tested is OUR logic: the never-raise
contract, drop-on-full, retry classification, flush, and the auth/URL rules.
"""
from uuid import uuid4

import httpx
import pytest

from inferscope.delivery import DeliveryOutcome, deliver
from inferscope.events import InferenceEvent
from inferscope.transport import HttpPublisher


def _event() -> InferenceEvent:
    request_id = str(uuid4())
    return InferenceEvent(
        payload={"request_id": request_id, "provider": "groq", "status": "success"},
        request_id=request_id,
    )


def _publisher(handler, **kwargs) -> HttpPublisher:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpPublisher("http://localhost:8081", client=client, **kwargs)


# --- config rules (D-015: self-host-first, no hosted default) -------------------


def test_base_url_is_required():
    with pytest.raises(ValueError, match="base_url is required"):
        HttpPublisher("")


def test_remote_collector_requires_an_api_key():
    with pytest.raises(ValueError, match="api_key is required"):
        HttpPublisher("https://collector.example.com")


def test_local_collector_may_omit_the_api_key():
    HttpPublisher("http://localhost:8081")  # must not raise


def test_remote_collector_with_key_is_accepted():
    p = HttpPublisher("https://collector.example.com", api_key="k")
    assert p._headers["Authorization"] == "Bearer k"


# --- the Publisher contract ----------------------------------------------------


@pytest.mark.asyncio
async def test_publish_delivers_the_event():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"deduped": False})

    p = _publisher(handler)
    await p.publish(_event())
    await p.flush()
    await p.aclose()

    assert len(seen) == 1
    assert seen[0].url.path == "/ingest"


@pytest.mark.asyncio
async def test_publish_never_raises_when_the_collector_is_down():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("collector unreachable")

    p = _publisher(handler)
    await p.publish(_event())  # the contract: telemetry must not break the caller
    await p.flush()
    await p.aclose()


@pytest.mark.asyncio
async def test_publish_never_raises_on_a_server_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    p = _publisher(handler)
    await p.publish(_event())
    await p.flush()
    await p.aclose()


@pytest.mark.asyncio
async def test_api_key_is_sent_as_a_bearer_token():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    p = HttpPublisher("http://localhost:8081", api_key="secret", client=client)
    await p.publish(_event())
    await p.flush()
    await p.aclose()

    assert seen == ["Bearer secret"]


@pytest.mark.asyncio
async def test_full_queue_drops_instead_of_blocking():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    # queue_size=1 with no drain running yet: the first goes in, the rest are shed.
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    p = HttpPublisher("http://localhost:8081", client=client, queue_size=1)
    p._queue.put_nowait(_event())  # pre-fill so the next publish finds it full

    await p.publish(_event())
    assert p.dropped == 1  # shed, not raised, not blocked

    await p.aclose()


@pytest.mark.asyncio
async def test_flush_waits_for_everything_queued():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    p = _publisher(handler)
    for _ in range(5):
        await p.publish(_event())
    await p.flush()

    assert len(seen) == 5
    await p.aclose()


# --- delivery classification ---------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, DeliveryOutcome.DELIVERED),
        (201, DeliveryOutcome.DELIVERED),
        (422, DeliveryOutcome.REJECTED),  # validation failure — permanent, drop
        (400, DeliveryOutcome.REJECTED),
        (500, DeliveryOutcome.FAILED),    # transient — retried, then given up on
    ],
)
async def test_deliver_classifies_the_response(status, expected):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        outcome = await deliver(client, "http://localhost:8081/ingest", _event())

    assert outcome is expected


@pytest.mark.asyncio
async def test_transient_failures_are_retried_then_classified_failed():
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        outcome = await deliver(client, "http://localhost:8081/ingest", _event())

    assert outcome is DeliveryOutcome.FAILED
    assert len(attempts) == 3  # _MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_rejections_are_not_retried():
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        return httpx.Response(422)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        outcome = await deliver(client, "http://localhost:8081/ingest", _event())

    assert outcome is DeliveryOutcome.REJECTED
    assert len(attempts) == 1  # a 4xx will never succeed; retrying is waste


@pytest.mark.asyncio
async def test_trace_id_is_propagated_as_a_header():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("x-trace-id"))
        return httpx.Response(200)

    event = _event()
    event.trace_id = "trace-abc"

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await deliver(client, "http://localhost:8081/ingest", event)

    assert seen == ["trace-abc"]
