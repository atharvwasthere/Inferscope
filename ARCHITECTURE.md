# Architecture Notes

[← back to README](./README.md)

How the system fits together, how a log flows through it, how it scales, and what it assumes when
things fail. Deep dives live alongside this file:

- **[Event Bus design](./docs/EVENT_BUS.md)** — why an event bus, options considered, Redis Streams + outbox.
- **[PII handling](./docs/PII.md)** — reversible tokenization, V1→V2→V3.
- **[Schema design](./docs/SCHEMA.md)** — table decisions, indexes, OTel alignment.

## Full pipeline

```
  ┌─────────┐   SSE    ┌──────────┐   LLM
  │ browser │ ◄──────► │ chatbot  │ ──────────► provider (Bedrock / Gemini / Groq)
  └─────────┘          │ +wrapper │
       ▲               └────┬─────┘
       │                    │ publish(event) → in-memory queue   [never blocks chat]
       │                    │ HttpPublisher: POST /ingest (retry)
       │                    ▼
       │             ┌───────────────────────────────── collector (ingestion) ─┐
       │             │  /ingest: validate (422 if bad) → 202 queued            │
       │             │        try XADD                 on error: INSERT        │
       │             │           ▼                               ▼             │
       │             │   [ redis stream ]               [ outbox_events ]      │
       │             │           │ XREADGROUP                    │ poll 10s    │
       │             │           ▼                               ▼             │
       │             │     IngestionWorker ──► persist ◄── OutboxPoller        │
       │             │                 redact + price + idempotent insert      │
       │             └───────────────────────────┬─────────────────────────────┘
       │                                         ▼
       │                                   ┌────────────┐
       └──── dashboard ◄────── reads ──────│  postgres  │
             (latency / throughput /       └────────────┘
              errors / cost)
```

The chatbot is a plain SDK user: it knows one URL (`INFERSCOPE_URL`) and nothing about Redis or
the outbox. Everything that makes delivery durable lives behind the collector.

## Ingestion flow

1. **Capture.** The SDK wrapper times every LLM call (latency, time-to-first-token, tokens, cost
   inputs, errors), mints a `request_id` client-side, and returns the result untouched.
2. **Hand off.** The event goes onto the SDK's in-memory queue (1000 slots) and `publish()` returns.
   It never blocks and never raises — if the queue is full the event is dropped and counted, because
   shedding telemetry beats slowing down a user's request.
3. **Send.** A background task POSTs queued events to the collector's `/ingest` (up to 8 at a time),
   retrying 3 times with exponential backoff and jitter.
4. **Accept.** `/ingest` validates the payload (Pydantic) and rejects a bad one with `422` while the
   sender is still listening. A good one is `XADD`ed to the Redis Stream — or written to
   `outbox_events` if Redis is down — and the route answers `202 queued`. **202 means queued, not
   stored.**
5. **Persist.** Inside the collector, the stream consumer (`XREADGROUP`/`XACK`) and the outbox poller
   both call one `persist_event()`: redact PII from previews, compute cost from the current
   `provider_models` rate, and insert with `ON CONFLICT (request_id) DO NOTHING` — idempotent, so
   redelivery is a no-op. A failed insert is not acked, so the event is retried.
6. **Read.** The dashboard runs read-only aggregations over `inference_logs` for latency p50/p95,
   throughput, error rate, and cost.

## Logging strategy

Structured JSON logs in every service with a `trace_id` on every line. The trace id is generated or
read from a request header by ASGI middleware, stored in a contextvar, and propagated chatbot →
wrapper → event → ingestion, so one id correlates a request end to end across process boundaries.
Inference telemetry itself is data, not logs — it lands in `inference_logs` and is queried by the
dashboard; application logs are for operational debugging.

## Scaling considerations

- **Stateless services.** chatbot, ingestion, and dashboard hold no per-request state beyond the DB,
  so each scales horizontally behind a load balancer.
- **Consumer groups.** Each ingestion replica runs a stream consumer. `XREADGROUP` splits events
  across them with a per-consumer pending list; add replicas to drain faster. `XAUTOCLAIM` reclaims
  work from a crashed replica.
- **Read/write split.** Today one asyncpg pool against one Postgres. At scale, point dashboard reads
  at a read replica so analytics scans never block ingestion writes. (See [SCHEMA.md](./docs/SCHEMA.md).)
- **Rollups.** Dashboard queries hit raw `inference_logs` (indexed, recent-first). At volume, an
  hourly rollup table / TimescaleDB continuous aggregate serves pre-computed p50/p95/cost while the
  raw table is kept for trace drill-down.
- **Bus graduation.** Redis Streams → Kafka when throughput exceeds a single node or several
  independent consumer groups are needed.

## Failure-handling assumptions

- **Telemetry is non-critical; the chat path is critical.** Publishing is best-effort and must never
  block or break a chat response. A failed publish is logged, not raised.
- **Durability starts at the 202.** Once `/ingest` answers, the event is in the Redis Stream or the
  outbox:
  - Redis down → `/ingest` writes the outbox; the poller persists it within 10s.
  - Postgres down → the event waits in the stream unacked and is persisted when Postgres is back.
- **Before the 202, the SDK holds events in memory.** If the collector is down longer than the 3
  retries, the event is dropped and logged. That's a deliberate trade — telemetry must never
  back-pressure the chat — and the fix, if it ever matters, is a small on-disk spool in the SDK.
- **Known gap: Redis *and* Postgres down.** The outbox write fails too, so the event is dropped and
  logged — but `/ingest` still answers `202`. It should answer `503` so the SDK retries.
- **At-least-once + idempotency = effectively-once.** The bus may deliver an event more than once;
  the unique `request_id` makes the duplicate a no-op. This is effectively-once *processing*, not
  true exactly-once *delivery* (which is impossible).
- **Rejection (bad data) is out of scope.** `/ingest` answers a bad payload with `422`, and the SDK logs
  and drops it; production would
  add a `dead_letter_events` table to park and replay it. The outbox solves delivery failure, a DLQ
  solves rejection failure — different problems. (See [EVENT_BUS.md](./docs/EVENT_BUS.md#the-dlq-gap-documented-not-built).)
- **Client disconnect mid-stream is safe.** The assistant reply is persisted in the streaming
  generator's `finally` block, so a dropped SSE connection still saves the turn.
