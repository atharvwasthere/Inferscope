# Architecture Notes

[← back to README](./README.md)

How the system fits together, how a log flows through it, how it scales, and what it assumes when
things fail. Deep dives live alongside this file:

- **[Event Bus design](./docs/EVENT_BUS.md)** — why an event bus, options considered, Redis Streams + outbox.
- **[PII handling](./docs/PII.md)** — reversible tokenization, V1→V2→V3.
- **[Schema design](./docs/SCHEMA.md)** — table decisions, indexes, OTel alignment.

## Full pipeline

```
  ┌─────────┐   LLM    ┌──────────┐
  │ browser │ ───────► │ chatbot  │ ──────────► provider (Bedrock / Gemini / Groq)
  └─────────┘   SSE    │ +wrapper │
       ▲               └────┬─────┘
       │ stream tokens      │ publish(event)   [best-effort, never blocks chat]
       │                    │
       │        ┌───────────┴────────────┐
       │   try XADD               on error: INSERT
       │        ▼                         ▼
       │  [ redis stream ]        [ outbox_events ]
       │        │ XREADGROUP              │ poll 10s
       │        ▼                         ▼
       │   IngestionWorker ─► deliver ◄─ OutboxPoller
       │                        │ POST /ingest (retry)
       │                        ▼
       │                  ┌───────────┐   redact + price + idempotent insert
       │                  │ ingestion │ ──────────────────────────────────┐
       │                  └───────────┘                                    ▼
       │                                                            ┌────────────┐
       └──────────────── dashboard  ◄────── reads ──────────────────│  postgres  │
                         (latency / throughput / errors / cost)     └────────────┘
```

## Ingestion flow

1. **Capture.** The SDK wrapper times every LLM call (latency, time-to-first-token, tokens, cost
   inputs, errors) and mints a `request_id` client-side, then returns the result untouched.
2. **Publish.** The wrapper fires an inference event onto the bus and moves on — fire-and-forget, so
   telemetry never adds latency to the user's request. If Redis is unreachable the event is written
   to the Postgres `outbox_events` table instead. (See [EVENT_BUS.md](./docs/EVENT_BUS.md).)
3. **Deliver.** A consumer-group worker (`XREADGROUP`/`XACK`) and the outbox poller both funnel
   events through one `deliver()` function that POSTs to ingestion with retry + backoff.
4. **Validate → redact → price → persist.** Ingestion validates the payload (Pydantic), redacts PII
   from previews, computes cost against the current `provider_models` rate, and inserts with
   `ON CONFLICT (request_id) DO NOTHING` — idempotent, so redelivery is a no-op.
5. **Read.** The dashboard runs read-only aggregations over `inference_logs` for latency p50/p95,
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
- **Consumer groups.** Redis Streams `XREADGROUP` distributes events across N ingestion workers with
  a per-consumer pending list; add replicas to drain faster. `XAUTOCLAIM` reclaims work from crashed
  workers.
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
- **Infrastructure failure is survivable, not lossy.** Redis down, ingestion down, or both → the
  producer writes to `outbox_events`; the poller drains it when services recover. Nothing is lost
  while infrastructure is unavailable.
- **At-least-once + idempotency = effectively-once.** The bus may deliver an event more than once;
  the unique `request_id` makes the duplicate a no-op. This is effectively-once *processing*, not
  true exactly-once *delivery* (which is impossible).
- **Rejection (bad data) is out of scope.** A permanent `422` is logged and dropped; production would
  add a `dead_letter_events` table to park and replay it. The outbox solves delivery failure, a DLQ
  solves rejection failure — different problems. (See [EVENT_BUS.md](./docs/EVENT_BUS.md#the-dlq-gap-documented-not-built).)
- **Client disconnect mid-stream is safe.** The assistant reply is persisted in the streaming
  generator's `finally` block, so a dropped SSE connection still saves the turn.
