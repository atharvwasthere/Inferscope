"""The collector.

Front door for SDK telemetry. The Redis/outbox pipeline now sits *behind* this
service rather than in front of it (D-006, D-014): the SDK POSTs here over HTTP,
the route validates and enqueues, and the drains running in this process persist
to Postgres.

That moves the meaning of a successful response. ``POST /ingest`` returns **202
queued**, not "stored" — durability from that point is owned by the Redis stream
with the Postgres outbox as its fallback, and idempotency (request_id UNIQUE +
ON CONFLICT DO NOTHING) makes redelivery harmless.
"""
import asyncio
import contextlib
import os
from contextlib import asynccontextmanager

import asyncpg
from fastapi import Depends, FastAPI

from inferscope.delivery import DeliveryOutcome
from inferscope.events import InferenceEvent
from ingestion.auth import require_api_key
from ingestion.db import close_pool, get_db, get_pool, init_pool
from ingestion.models import InferenceLogPayload
from ingestion.persist import persist_event
from obs.log import configure_logging, get_logger
from obs.middleware import TraceIdMiddleware
from redis_bus.consumer import IngestionWorker
from redis_bus.outbox_poller import OutboxPoller
from redis_bus.producer import RedisProducer

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379")

configure_logging("ingestion")
logger = get_logger("ingestion")

# Enqueue side of the pipeline. Falls back to the Postgres outbox when Redis is
# unreachable, so a Redis outage degrades throughput rather than losing events.
producer = RedisProducer(REDIS_URL, pool_getter=get_pool)


async def _handle(event: InferenceEvent) -> DeliveryOutcome:
    """The EventHandler both drains call — persist, or say why it could not."""
    pool = get_pool()
    if pool is None:
        return DeliveryOutcome.FAILED
    return await persist_event(pool, event)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_pool()

    # Both drains now live here, next to the database they write to:
    #   - consumer: the Redis Streams happy path
    #   - poller:   the Postgres outbox fallback path
    worker = IngestionWorker(REDIS_URL, _handle)
    poller = OutboxPoller(get_pool, _handle)
    tasks = [asyncio.create_task(worker.run()), asyncio.create_task(poller.run())]

    yield

    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await worker.close()
    await poller.close()
    await producer.close()
    await close_pool()


app = FastAPI(title="inferscope-ingestion", lifespan=lifespan)
app.add_middleware(TraceIdMiddleware)


@app.get("/health")
async def health() -> dict:
    """Liveness — process is up."""
    return {"status": "ok"}


@app.get("/ready")
async def ready(db: asyncpg.Connection = Depends(get_db)) -> dict:
    """Readiness — DB reachable."""
    await db.fetchval("SELECT 1")
    return {"status": "ready"}


# Auth is a route dependency, not middleware, so /health and /ready stay open for
# Kubernetes probes — which have no credentials and no reason to need any.
@app.post("/ingest", status_code=202, dependencies=[Depends(require_api_key)])
async def ingest(payload: InferenceLogPayload) -> dict:
    """Validate at the trust boundary, then enqueue.

    Validation stays here so a malformed payload is rejected with 422 while the
    caller is still listening, rather than being queued and silently dropped by a
    drain later. ``mode="json"`` because the payload has to survive a JSON round
    trip through Redis — UUIDs and datetimes must already be strings.
    """
    event = InferenceEvent.from_log(payload.model_dump(mode="json"))
    await producer.publish(event)
    return {"request_id": str(payload.request_id), "status": "queued"}
