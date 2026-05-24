# Schema design

[← back to README](../README.md) · [Architecture overview](../ARCHITECTURE.md)

Four core tables: `conversations`, `messages`, `inference_logs`, `provider_models`
(plus `outbox_events` for the [event-bus fallback](./EVENT_BUS.md)). The full DDL lives in
`backend/alembic/versions/0001_initial_schema.py` — a single consolidated baseline.

## Table decisions

- **UUID primary keys.** The SDK generates `request_id` client-side before the call fires. No
  database round-trip to mint an ID, and IDs are globally unique across services — which matters the
  moment ingestion is horizontally scaled.
- **`started_at` vs `created_at`.** `started_at` is when the LLM call fired (set by the SDK);
  `created_at` is when ingestion stored the row (DB default). The difference is your pipeline lag —
  directly observable without extra instrumentation.
- **Denormalized `total_tokens`.** Stored, not computed. Every dashboard query would otherwise
  re-sum input + output tokens across millions of rows. Compute once at write, read cheap forever.
- **`provider_models` pricing table.** Cost rates live in a table, not in code. Pricing changes are
  a row update, not a deploy. Cost is computed at ingestion time against the current rate and frozen
  onto the row. This table is also the single source of truth for the `GET /models` catalog the
  frontend renders.
- **`ON CONFLICT (request_id) DO NOTHING`.** Ingestion is idempotent. A retried log (network blip,
  SDK retry, event-bus redelivery) hits the unique constraint and is silently dropped — no duplicate
  row, no error. See [EVENT_BUS.md](./EVENT_BUS.md) for why this is paired with at-least-once delivery.
- **`trace_id` column.** Ties a chatbot request to its ingestion row to its provider call, end to
  end. One ID across the whole pipeline, propagated by header and stored on the row.
- **`pii_map` JSONB on `messages`.** Reversible PII tokenization — see [PII.md](./PII.md).
- **`raw_usage` / `attributes` JSONB.** Provider-native usage blocks are stored verbatim alongside
  the normalized canonical columns, so the schema never loses fidelity and never has to be migrated
  when a provider adds a new usage field.

## OpenTelemetry alignment

The inference log schema loosely follows
[OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
(`gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.client.time_to_first_token`,
etc.). This keeps the columns vendor-neutral and means the logs could feed an OTel collector later
without renaming.

## Indexes

- `idx_logs_created_at`, `idx_logs_status`, `idx_logs_provider` — every dashboard query filters or
  sorts on these. Composite with `created_at DESC` because the dashboard always scans recent-first.
- `idx_messages_pii` — partial GIN index `WHERE pii_map IS NOT NULL`, so clean messages cost nothing
  and PII audits stay fast.
- `idx_outbox_unprocessed` — partial index `WHERE processed = FALSE`, so the outbox poller's
  unprocessed-rows scan stays cheap regardless of total table size.

## Schema-related tradeoffs

**DB keys: UUID vs serial int** — UUID. The SDK mints `request_id` client-side with no DB
round-trip, and IDs stay unique across services. *Production:* same.

**Dashboard queries: raw table vs rollup** — query raw `inference_logs` with an indexed
`created_at`. Sub-100ms at demo scale; the indexes cover every dashboard query. *Production:* an
hourly rollup table populated by a background job, serving pre-aggregated p50/p95/cost, with the raw
table kept for trace drill-down. TimescaleDB continuous aggregates automate the rollup.

**DB connection: single pool vs read/write split** — a single asyncpg pool with `command_timeout=30`
and `max_inactive_connection_lifetime=300`. One Postgres instance; split pools only earn their keep
with a read replica. *Production:* read replica for dashboard reads, with separate pools pointing at
primary (writes) and replica (reads), so dashboard scans never block ingestion writes.
