import asyncio
import contextlib
import os
from contextlib import asynccontextmanager
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from chatbot.abort_bus import RedisAbortBus, StreamRegistry
from chatbot.conversations import router as conversations_router
from chatbot.db import close_pool, get_db, get_pool, get_provider_models, init_pool
from chatbot.services.chat_service import ChatService
from obs.log import configure_logging
from obs.middleware import TraceIdMiddleware
from redis_bus.consumer import IngestionWorker
from redis_bus.outbox_poller import OutboxPoller
from redis_bus.producer import RedisProducer

# isolated import boundary — chatbot uses only sdk.wrapper
from sdk.wrapper import LLMWrapper

INGESTION_URL = os.environ.get("INGESTION_URL", "http://ingestion:8081/ingest")
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379")
DEFAULT_PROVIDER = os.environ.get("DEFAULT_PROVIDER", "bedrock")
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "anthropic.claude-sonnet-4-6")
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")

configure_logging("chatbot")

# Producer publishes to Redis; the wrapper depends only on the Publisher abstraction.
producer = RedisProducer(REDIS_URL, pool_getter=get_pool)
wrapper = LLMWrapper(publisher=producer)

# Stream-abort plumbing:
#   - registry: per-process map of stream_id -> asyncio.Event (the generator polls it).
#   - abort_bus: Redis pub/sub fan-out so /abort lands on any replica and the
#     replica that owns the stream task flips its local event.
# Wired together in lifespan(): abort_bus.start(registry.cancel).
registry = StreamRegistry()
abort_bus = RedisAbortBus(REDIS_URL)


class ChatRequest(BaseModel):
    message: str
    conversation_id: UUID | None = None
    provider: str = DEFAULT_PROVIDER
    model: str = DEFAULT_MODEL


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_pool()

    # Two delivery drains, both reaching ingestion via inferscope.delivery:
    #   - consumer: the Redis Streams happy path
    #   - poller:   the Postgres outbox fallback path
    worker = IngestionWorker(REDIS_URL, INGESTION_URL)
    poller = OutboxPoller(get_pool, INGESTION_URL)
    tasks = [asyncio.create_task(worker.run()), asyncio.create_task(poller.run())]

    # Start the abort subscriber: every replica listens on the shared channel and
    # asks its local registry to cancel — the replica that doesn't own the stream
    # no-ops.
    await abort_bus.start(registry.cancel)

    yield

    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await worker.close()
    await poller.close()
    await abort_bus.close()
    await producer.close()
    await close_pool()


app = FastAPI(title="inferscope-chatbot", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Trace-Id"],
)
app.add_middleware(TraceIdMiddleware)
app.include_router(conversations_router)


@app.get("/health")
async def health() -> dict:
    """Liveness — process is up."""
    return {"status": "ok"}


@app.get("/ready")
async def ready(db: asyncpg.Connection = Depends(get_db)) -> dict:
    """Readiness — DB reachable."""
    await db.fetchval("SELECT 1")
    return {"status": "ready"}


@app.get("/models")
async def models(db: asyncpg.Connection = Depends(get_db)) -> dict:
    """Available models grouped by provider — from provider_models (single source of truth)."""
    rows = await get_provider_models(db)
    grouped: dict[str, list[str]] = {}
    for r in rows:
        grouped.setdefault(r["provider"], []).append(r["model"])
    return grouped


@app.post("/chat/stream")
async def chat_stream(
    req: ChatRequest, db: asyncpg.Connection = Depends(get_db)
) -> StreamingResponse:
    service = ChatService(wrapper=wrapper, db=db, registry=registry)
    conversation_id, messages = await service.prepare(
        req.message, req.conversation_id, req.provider
    )
    return StreamingResponse(
        service.stream_tokens(conversation_id, messages, req.provider, req.model),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/streams/{stream_id}/abort")
async def abort_stream(stream_id: UUID) -> dict:
    """Broadcast an abort for a running stream. Returns immediately; the
    owner replica flips its local event and the generator breaks within a
    token's worth of latency, then persists the partial reply and closes
    the provider socket (so the provider stops generating/billing)."""
    await abort_bus.publish(stream_id)
    return {"stream_id": str(stream_id), "status": "abort_requested"}
