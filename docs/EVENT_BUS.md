# Event Bus — design & decision record

[← back to README](../README.md) · [Architecture overview](../ARCHITECTURE.md)

This document explains how inference logs travel from the SDK to the database,
why that path is an event bus rather than a direct HTTP call, and how the design
fails safely. It is a decision record, not a tutorial.

## 1. Why event-based

The SDK wrapper measures every LLM call — latency, tokens, cost, errors — and that
telemetry has to land in Postgres. The naive approach is a direct HTTP POST from the
wrapper to the ingestion service:

```
  Before (direct HTTP):

  chatbot ── LLM call ──► provider
     │
     └── POST /ingest ──► ingestion ──► postgres
            (in the chat request's lifecycle)
```

This couples the chat path to ingestion. The wrapper has to know the ingestion URL.
If ingestion is slow or down, that pressure is felt on the path that serves the user —
the very place latency is least acceptable. Telemetry, which is non-critical, can
degrade the product, which is critical. That is backwards.

An event bus inverts the dependency. The producer announces "an inference happened"
and moves on. A separate consumer delivers it to ingestion, on its own schedule, with
its own retries.

```
  After (event bus):

  chatbot ── LLM call ──► provider
     │
     └── publish(event) ──► [ bus ] ──► consumer ──► ingestion ──► postgres
            (fast, fire-and-forget)        (decoupled, retries here)
```

The producer and consumer no longer share a lifecycle. Ingestion downtime backs up
in the bus, not in the chat request.

## 2. Options considered

| Option | Verdict |
|---|---|
| `asyncio.Queue` | ✗ in-process only, no persistence — dies with the process |
| Postgres `LISTEN/NOTIFY` | ✗ uses the primary DB as a broker, 8KB payload limit, no consumer groups |
| Webhook (`POST /events` → router) | ✗ an HTTP proxy, not a bus — adds a SPOF, no persistence, no replay |
| Celery + Redis | ✗ task-queue semantics ("run this") not event semantics ("this happened"), no native replay |
| **Redis Streams** | **✓ chosen — persistent, consumer groups, replay, at-least-once, one container** |
| Kafka | ✗ now / ✓ later — correct at scale, operationally overkill for this workload |

## 3. Why Redis Streams specifically

- **Persistence.** With `appendonly yes` the stream survives a Redis restart. An
  `asyncio.Queue` loses everything on process death; a Stream does not.
- **Consumer groups.** `XREADGROUP` lets multiple workers share one stream, each
  getting a disjoint slice, with a per-consumer Pending Entries List (PEL) tracking
  unacknowledged messages. This is how delivery is made reliable and horizontally
  scalable at the same time.
- **Replay.** Messages stay in the stream until trimmed (`maxlen ~ 10000`), so a
  consumer that fell behind can re-read, and `XAUTOCLAIM` reclaims messages stranded
  by a crashed worker.
- **At-least-once.** A message is only removed from a consumer's PEL on `XACK`. Crash
  before ack and it is redelivered.
- **One container.** A single `redis:7-alpine` service. No Zookeeper, no brokers, no
  partitions to plan.

## 4. Idempotency and Redis Streams are intentionally paired

> Redis Streams gives at-least-once delivery. `request_id` `ON CONFLICT DO NOTHING`
> ensures retries never create duplicates. The two patterns are designed together.

At-least-once means a message can be delivered more than once — on retry, on reclaim,
on a consumer crash between POST and `XACK`. On its own that would double-count tokens
and cost. The ingestion table has a unique `request_id` and inserts with
`ON CONFLICT (request_id) DO NOTHING`, so a second delivery of the same event is a
no-op. Neither pattern is complete alone:

- Redis Streams without idempotency → duplicate rows on every retry.
- Idempotency without a redelivering bus → nothing to protect against.

The SDK mints `request_id` client-side before the call, so the idempotency key exists
from the very first publish.

## 5. Production graduation path

```
  asyncio.Queue  ──►  Redis Streams  ──►  Kafka
  (where we were)     (where we are)      (where we'd go)
```

- **asyncio.Queue → Redis Streams.** Trigger: you need delivery to survive a process
  restart, or you need more than one worker draining in parallel. (This change.)
- **Redis Streams → Kafka.** Trigger: throughput approaches Redis's single-node
  ceiling, *or* you need several independent consumer groups (ingestion, real-time
  alerting, a data-lake sink) each with its own offset, *or* you need long retention
  / replay measured in days rather than a capped stream length.

Building Kafka now would be three more containers and partition planning for a
workload that a single Redis node serves comfortably.

## 6. Failure handling

The happy path makes Redis a single point of failure: if `XADD` fails, the event is
gone. So the producer has a second, durable path.

```
                       publish(event)
                            │
              ┌─────────────┴──────────────┐
       try XADD                   on ANY redis error
              │                            │
              ▼                            ▼
     [ redis stream ]            INSERT outbox_events (processed = FALSE)
              │                            │
        XREADGROUP                  poll every 10s
              │                            │
              ▼                            ▼
        IngestionWorker ──► deliver() ◄── OutboxPoller
                              │
                              ▼
                         ingestion ──► inference_logs
                              │  ON CONFLICT (request_id) DO NOTHING
              ┌───────────────┼───────────────┐
         2xx → done      422 → drop+warn   5xx → leave (redeliver)
                              │
                              ▼
                      ✗ no DLQ — rejected payload is logged and dropped
```

- **Redis down / ingestion down / both down.** The producer catches the `XADD`
  failure and writes to the `outbox_events` table instead. The `OutboxPoller` drains
  unprocessed rows every 10 seconds and delivers them once services recover. Nothing
  is lost while infrastructure is unavailable.
- **No duplicated logic.** Both the Redis consumer and the outbox poller deliver
  through the same `redis_bus.delivery.deliver` function. They differ only in where
  the event came from; the POST, retry policy, and response classification are shared.
- **Double delivery is safe.** If Redis recovers and redelivers an event that the
  outbox already delivered (or vice versa), ingestion's `request_id` idempotency makes
  the second one a no-op.
- **The outbox is staging, not storage.** Rows are written only on the failure path
  and marked `processed = TRUE` once delivered. A periodic job deletes processed rows
  older than 24h (out of scope for this build).

### The DLQ gap (documented, not built)

The outbox handles **delivery** failure — Redis or ingestion being unreachable. It does
**not** handle **rejection** failure — ingestion accepting the connection but returning
a permanent `422` because the payload is invalid (schema drift, a bug in the producer).
Today such an event is logged with its `request_id` and dropped:

```
  delivery succeeds, ingestion rejects (422)
     → log warning with request_id
     → mark processed / XACK
     → event is gone, no way to investigate or replay
```

The production fix is a dead-letter queue: a `dead_letter_events` table that parks
rejected payloads instead of dropping them, so they can be inspected and replayed after
the schema or bug is fixed. Outbox and DLQ solve different problems — infrastructure
failure versus bad data — and only the first is in scope here.
