# inferscope

## Overview

inferscope is an LLM observability and inference logging platform. It sits between your application and the model providers, capturing every inference call — latency, time-to-first-token, token usage, cost, and errors — without adding meaningful latency to the request path. It ships with a multi-provider chatbot (Bedrock, Gemini, and Groq) as the reference workload, a fire-and-forget SDK that instruments calls transparently, an ingestion service that normalizes and persists logs, and a real-time dashboard for latency, throughput, error rate, and cost. It is built for teams running LLMs in production who need to answer "what did this cost, how slow was it, and why did it fail" — per request, per model, per provider.

## Architecture

```
frontend ──> chatbot ──> sdk/wrapper ──> LLM API (Bedrock / Gemini / Groq)
                  │
                  └──> ingestion ──> postgres
                                       │
                       dashboard ──────┘
```

- **frontend** — React UI: streaming chat, conversation history, metrics dashboard.
- **chatbot** — FastAPI service owning conversation state and multi-turn context; calls the SDK.
- **sdk/wrapper** — provider-agnostic interceptor: measures the call, fires a log, returns the result untouched.
- **ingestion** — validates, redacts PII, computes cost, and persists logs idempotently.
- **dashboard** — read-only aggregation API over the inference log table.
- **postgres** — single source of truth for conversations, messages, logs, and pricing.

The codebase follows SOLID principles and Clean Architecture boundaries. Each service uses the **Service/Repository pattern** — route handlers stay thin, a service layer orchestrates flow, and a repository layer (`db.py`) is the only place that touches SQL. Providers follow the **Provider pattern** behind a registry, so adding a model vendor is one dictionary entry and a new file, with zero changes to the wrapper.

The inference log schema loosely follows [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) (`gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.client.time_to_first_token`, etc.). Provider-native usage blocks are stored verbatim in a `raw_usage` JSONB column alongside the normalized canonical columns — so the schema never loses fidelity and never has to be migrated when a provider adds a new field.

### Project layout

```
inferscope/
├── backend/              # Python services — one shared codebase, three FastAPI apps
│   ├── chatbot/          # chat, conversations, SSE streaming, LLM-generated titles
│   ├── ingestion/        # validate → redact → price → persist (idempotent)
│   ├── dashboard/        # read-only metrics aggregation
│   ├── sdk/              # provider-agnostic wrapper + providers/ + PII tokenizer/redactor
│   ├── redis_bus/        # Redis Streams producer/consumer + Postgres outbox fallback
│   ├── obs/              # structured JSON logging + trace-id propagation
│   ├── alembic/          # migrations (single 0001 baseline)
│   ├── alembic.ini
│   └── .env.example
├── frontend/             # React 19 + Vite + TanStack Query
├── docs/                 # deep dives — EVENT_BUS.md, PII.md, SCHEMA.md
├── k8s/                  # Kubernetes (Minikube) manifests
├── ARCHITECTURE.md       # architecture notes (pipeline, scaling, failure handling)
├── docker-compose.yml
├── Makefile
└── README.md
```

## Docs

- [Architecture Notes](./ARCHITECTURE.md) — pipeline, ingestion flow, scaling, failure handling
- [Event Bus design](./docs/EVENT_BUS.md) — why an event bus, Redis Streams + Postgres outbox
- [PII handling](./docs/PII.md) — reversible tokenization (V1 → V2 → V3)
- [Schema design](./docs/SCHEMA.md) — table decisions, indexes, OTel alignment
- [Deployment](./DEPLOYMENT.md) — Docker Compose + self-hosted Kubernetes (k3s on Azure)

## Quick Start

```bash
cp backend/.env.example backend/.env
# fill in AWS / Google / Groq credentials
docker compose up --build
# open http://localhost:3000
```

## Tech Stack

| Service | Language / Framework | Key Libraries |
|---|---|---|
| chatbot | Python / FastAPI | aioboto3, google-genai, groq, asyncpg, tenacity, redis |
| event bus | Redis Streams | redis:7-alpine (AOF) + Postgres outbox fallback |
| ingestion | Python / FastAPI | asyncpg, pydantic, tenacity |
| dashboard | Python / FastAPI | asyncpg |
| frontend | React 19 | Vite, TanStack Query v5, recharts |
| database | PostgreSQL 16 | Alembic migrations |
| infra | Docker Compose, Kubernetes (k3s) | Docker Hub registry, Azure VM (Ubuntu 24.04) |

## Schema Design Decisions

Four core tables — `conversations`, `messages`, `inference_logs`, `provider_models` — plus
`outbox_events` for the event-bus fallback. Highlights:

- **UUID primary keys** minted client-side by the SDK (`request_id`) — no DB round-trip, globally unique across services.
- **`ON CONFLICT (request_id) DO NOTHING`** makes ingestion idempotent — retries and redeliveries never duplicate.
- **`provider_models` pricing table** — rates are data, not code; a pricing change is a row update, not a deploy. Also the source of truth for the `GET /models` catalog.
- **`raw_usage` / `attributes` JSONB** keep provider-native fidelity beside the normalized columns; **`pii_map` JSONB** powers reversible PII tokenization.
- **`started_at` vs `created_at`** exposes pipeline lag for free; **`trace_id`** correlates a request end to end.

→ Full rationale, indexes, and OTel alignment in **[docs/SCHEMA.md](./docs/SCHEMA.md)**.

## PII Handling

Conversation messages are stored with **reversible tokenization**: each PII span becomes a stable
`[PII:TYPE:N]` token and the originals live in a nullable `pii_map` JSONB column. The **LLM always
sees tokens**; the **user always sees originals** (detokenized only at the display route). This was
chosen over irreversible redaction because redaction breaks multi-turn context the conversation
depends on.

→ The full V1 → V2 → V3 story and the JSONB/boundary rationale in **[docs/PII.md](./docs/PII.md)**.

## Tradeoffs Made

**Event bus: Redis Streams + Postgres outbox fallback** (see [docs/EVENT_BUS.md](./docs/EVENT_BUS.md))
- *Decision:* the SDK publishes each inference event to a Redis Stream (`XADD`); a consumer group worker drains it (`XREADGROUP`/`XACK`) and POSTs to ingestion with retry. If Redis is unreachable, the producer falls back to an `outbox_events` table that a poller drains when services recover.
- *Why:* decouples the chat path from ingestion availability, survives process/Redis restarts (AOF persistence), supports horizontal workers via consumer groups, and gives at-least-once delivery. Paired with `request_id` `ON CONFLICT DO NOTHING` so redelivery never duplicates.
- *Production:* graduate to Kafka when throughput exceeds a single Redis node or multiple independent consumer groups are needed. Add a `dead_letter_events` table for permanently-rejected (422) payloads — see the DLQ note below.

**Failure handling: outbox vs DLQ**
- `outbox_events` handles **infrastructure** failures — Redis or ingestion downtime. `422` validation rejections are logged with `request_id` and dropped. Production would add a `dead_letter_events` table to park rejected payloads for investigation and replay after schema fixes. Outbox solves delivery failure; DLQ solves rejection failure — different problems.

**Streaming: SSE vs WebSocket**
- *Decision:* SSE.
- *Why:* token streaming is unidirectional server push. SSE is HTTP/1.1-compatible and needs no connection-management layer.
- *Production:* same — SSE is the correct primitive for this use case.

**PII redaction: regex vs NLP**
- *Decision:* regex patterns for structured PII — email, phone, credit card, SSN, Aadhaar, PAN.
- *Why:* microsecond latency, zero dependencies, covers the structured cases that actually leak.
- *Production:* Microsoft Presidio for unstructured PII (names, locations), gated behind `ENABLE_NLP_REDACTION=true`. Redaction runs server-side at ingestion so the SDK cannot be bypassed by a misbehaving client.

**Bedrock client: boto3 vs aioboto3**
- *Decision:* aioboto3.
- *Why:* true async. boto3 wrapped in `run_in_executor` fakes async and bottlenecks on the thread pool under concurrent load.
- *Production:* same.

**DB keys: UUID vs serial int**
- *Decision:* UUID.
- *Why:* the SDK mints `request_id` client-side with no DB round-trip, and IDs stay unique across services.
- *Production:* same.

**Dashboard refresh: polling vs WebSocket**
- *Decision:* polling every 30s via TanStack Query `refetchInterval`.
- *Why:* 30s staleness is fine for an observability dashboard. WebSocket adds connection management for no visible benefit.
- *Production:* same, or drop to 10s.

**DB connection: single pool vs read/write split**
- *Decision:* a single asyncpg pool with `command_timeout=30` and `max_inactive_connection_lifetime=300`.
- *Why:* one Postgres instance. Split pools only earn their keep with a read replica.
- *Production:* read replica for dashboard reads, with separate pools pointing at primary (writes) and replica (reads). Stops dashboard scans from blocking ingestion writes.

**Dashboard queries: raw table vs rollup**
- *Decision:* query raw `inference_logs` with an indexed `created_at`.
- *Why:* sub-100ms at demo scale; the indexes cover every dashboard query.
- *Production:* an hourly rollup table populated by a background job, serving pre-aggregated p50/p95/cost. The raw table is kept for trace drill-down. TimescaleDB continuous aggregates automate the rollup.

**Circuit breaker / bulkhead**
- *Decision:* not implemented.
- *Why:* a circuit breaker guards against cascading failure under load; a bulkhead stops a slow provider starving a fast one. Neither has an observable effect with a single user.
- *Production:* a per-provider `asyncio.Semaphore` bulkhead (~5 lines) and a circuit breaker with a failure threshold and timeout window wrapping the `PROVIDERS` registry.

**K8s: Minikube vs cloud cluster**
- *Decision:* Minikube.
- *Why:* a self-hosted, local cluster the evaluator can run without cloud access. The manifests are cloud-agnostic.
- *Production:* the same manifests targeting EKS/GKE — change `imagePullPolicy`, add an Ingress with TLS, and add an HPA on ingestion.

## Production Patterns Implemented

- Idempotent ingestion via `ON CONFLICT (request_id) DO NOTHING`.
- Retry with exponential backoff and jitter on log delivery.
- Trace ID propagation across chatbot → wrapper → ingestion.
- Structured JSON logging with `trace_id` in every service.
- Liveness (`/health`) vs readiness (`/ready`) split — k8s probes use separate endpoints.
- asyncpg connection pool with `command_timeout` — prevents idle-in-transaction accumulation.
- Provider pattern with a registry — adding a provider is one dict entry, zero changes to the wrapper.
- Repository pattern — `db.py` is the only layer touching SQL.
- Service layer — `ChatService` orchestrates flow; route handlers stay thin.
- Full package imports plus `PYTHONPATH` — identical invocation across local dev and Docker.

## What I'd Improve With More Time

- Dead-letter queue (`dead_letter_events`) for permanently-rejected payloads — the outbox already covers infrastructure failure.
- Circuit breaker plus per-provider bulkhead in the wrapper.
- Microsoft Presidio for NLP-based PII detection.
- Hourly rollup table and TimescaleDB continuous aggregates for the dashboard at scale.
- Time-based partitioning on `inference_logs` by month.
- Per-user cost attribution and budget alerting.
- Alembic autogenerate — migrations are currently hand-written on purpose, since the services use asyncpg directly and no SQLAlchemy ORM models exist to diff against.
- Ingress with TLS on k8s instead of NodePort.
- HPA on the ingestion service for burst traffic.

## Deployment

Two targets — local Docker Compose (above) and a self-hosted **k3s** cluster on an Azure VM
(images via Docker Hub). Build/push with `make push`, deploy with `make deploy`:

```bash
make push      # build 4 images → Docker Hub (needs `docker login`)
make deploy    # apply manifests to the k3s cluster (namespace → data → migrate Job → services)
# app at http://<VM_PUBLIC_IP>:30000
```

→ Full guide — Azure VM creation, k3s install, the five Minikube→cloud fixes, and the
nginx reverse-proxy / 12-factor reasoning — in **[DEPLOYMENT.md](./DEPLOYMENT.md)**.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | yes | Postgres connection string |
| `INGESTION_URL` | yes | Ingestion service URL |
| `REDIS_URL` | yes | Event-bus Redis URL (`redis://redis:6379`; local: `redis://localhost:6379`) |
| `AWS_REGION` | yes | Bedrock region |
| `AWS_ACCESS_KEY_ID` | yes | AWS credentials |
| `AWS_SECRET_ACCESS_KEY` | yes | AWS credentials |
| `GOOGLE_API_KEY` | yes | Gemini API key |
| `GROQ_API_KEY` | yes | Groq API key |
| `SYSTEM_PROMPT` | no | Default: "You are a helpful assistant" |
| `MAX_TURNS` | no | Default: 10 — conversation context window |
