# Event Bus — design & decision record

[← back to README](../README.md) · [Architecture overview](../ARCHITECTURE.md)

This document explains how inference logs travel from the SDK to the database,
why that path has an event bus in it, and how the design fails safely. It is a
decision record, not a tutorial.

> **Update (21 Oct 2026):** the bus now sits *behind* the collector. The chatbot POSTs
> events to `/ingest`; the collector puts them on the stream and its own drains persist
> them. Sections 1 and 6 show the current shape, and section 7 explains the move.

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
and moves on. A separate consumer persists it, on its own schedule, with its own
retries.

```
  Now (SDK → collector → bus):

  chatbot ── LLM call ──► provider
     │
     └── publish(event) ──► SDK queue ──► POST /ingest ──► [ bus ] ──► drains ──► postgres
            (in memory, never blocks)     (202 queued)     inside the collector
```

The chat request and the database no longer share a lifecycle. The SDK hands the
event to a background queue, the collector accepts it in milliseconds, and a slow
or down Postgres backs up in the bus, not in the chat request.

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
on a consumer crash between the insert and `XACK`. On its own that would double-count tokens
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
gone. So `/ingest` has a second, durable path.

```
         SDK (chatbot) ── POST /ingest, 3 tries with backoff
                                  │
                                  ▼
               collector: validate (bad payload → 422)
                                  │
              ┌───────────────────┴─────────────────┐
          try XADD                         on ANY redis error
              │                                     │
              ▼                                     ▼
      [ redis stream ]          INSERT outbox_events (processed = FALSE)
              │                                     │
         XREADGROUP                          poll every 10s
              │                                     │
              ▼                                     ▼
       IngestionWorker ──► persist_event() ◄── OutboxPoller
                                  │  ON CONFLICT (request_id) DO NOTHING
                  ┌───────────────┼──────────────────┐
            stored → ack   bad data → drop   DB error → retry
```

What each failure does today:

| What is down | What happens | Event |
|---|---|---|
| Nothing | `XADD` → consumer persists → `XACK` | 1 row |
| Redis | `/ingest` writes the outbox → poller persists it | 1 row |
| Postgres | Event waits in the stream unacked → persisted when Postgres is back | 1 row, later |
| Collector | SDK retries 3 times, then logs and drops — its queue is in memory | lost |
| Redis **and** Postgres | Outbox write fails → logged and dropped, but `/ingest` still says `202` | lost |

- **No duplicated logic.** The stream consumer and the outbox poller call the same
  `persist_event()`. They differ only in where the event came from.
- **Double delivery is safe.** If an event comes through both paths, or is redelivered
  after a crash, the unique `request_id` makes the second insert a no-op.
- **The outbox is staging, not storage.** Rows are written only on the failure path and
  marked `processed = TRUE` once persisted. A periodic job deletes processed rows older
  than 24h (out of scope for this build).
- **Two gaps, written down.** The SDK has no disk spool, so a long collector outage loses
  events. And when both Redis and Postgres are down, `/ingest` should answer `503`, not
  `202`, so the SDK retries instead of believing the event was queued.

### The DLQ gap (documented, not built)

The outbox handles **delivery** failure — Redis or Postgres being unreachable. It does
**not** handle **rejection** failure — a payload that is invalid (schema drift, a bug in
the producer). `/ingest` answers such a payload with `422` and the SDK drops it; an event
that reaches a drain and still fails validation is acked and dropped. Either way it is
logged with its `request_id` and gone:

```
  the drain reads the event, persist_event() rejects it (invalid)
     → log warning with request_id
     → mark processed / XACK
     → event is gone, no way to investigate or replay
```

The production fix is a dead-letter queue: a `dead_letter_events` table that parks
rejected payloads instead of dropping them, so they can be inspected and replayed after
the schema or bug is fixed. Outbox and DLQ solve different problems — infrastructure
failure versus bad data — and only the first is in scope here.

## 7. Why the bus moved behind the collector

The first version ran the producer and both drains inside the chatbot. They POSTed to
ingestion over HTTP. That meant the chatbot had to know about Redis, the outbox and the
ingestion URL — three things an app using an observability SDK should not care about.

```
  Before:  chatbot ─► XADD ─► [ bus ] ─► drains (in chatbot) ─► POST /ingest ─► postgres
  Now:     chatbot ─► POST /ingest ─► [ bus ] ─► drains (in collector) ─► postgres
```

- **The app knows one URL.** The chatbot is now a plain SDK user (`INFERSCOPE_URL`).
  Swapping the transport needed no change to the wrapper, because it only depends on the
  `Publisher` interface.
- **Durability sits next to the database.** The drains run in the collector's lifespan,
  beside the Postgres they write to. "Delivery" became a local function call instead of an
  HTTP hop.
- **Validation stays at the door.** `/ingest` still rejects a bad payload with `422` while
  the sender is listening, instead of queueing it and dropping it later.
- **The cost:** `202` is a promise, not a receipt. Durability now starts at the collector,
  so the SDK's in-memory queue is the weak spot (see section 6).
