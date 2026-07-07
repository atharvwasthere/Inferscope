# I Built an LLM Observability Platform in Nine Days. Here's the System Design, and Every Decision I Reversed.

Line one of `.env.example`. A live Postgres password. In a public GitHub repo. Since the first commit. I found it during a `git grep` at 2am on day eight.

I'll tell you exactly how that happened — it's the most instructive failure in the whole project. But it was also the *last* thing that went wrong, and to understand why it stung, you need the eight days in front of it. So back up.

The assignment said "build a lightweight inference logging and ingestion system." Chatbot, wrapper, ingestion pipeline, dashboard. I estimated a weekend. I shipped nine days later with Redis Streams, a transactional outbox, reversible PII tokenization backed by JSONB vaults, an anti-corruption layer across three model providers, and a self-hosted Kubernetes cluster on Azure behind a real TLS domain. The architecture notes are 240 lines on their own.

That's not over-engineering. Over-engineering is solving problems you don't have. Every piece here exists because I asked one more question than "does it run," and the honest answer was "no, not when X." This is the system design, walked the way you'd walk it on a whiteboard — and at each stage, the decision I started with and the one I ended with, because they were rarely the same.

---

## Requirements

You can't design until you know what you're optimizing for, and the most useful thing I did was separate what the system must *do* from how it must *behave*.

**Functional — what it does.** A chatbot with multi-turn memory and a UI. An SDK that wraps every model call and captures metadata: model, provider, latency, time-to-first-token, token usage, cost, status, errors, conversation ID, input/output previews. An ingestion service that validates, enriches, and stores those logs. A database holding chat messages, inference logs, and extracted metadata. A dashboard for latency, throughput, error rate, and cost. The bonus tier piled on more: multi-provider, streaming, event-driven architecture, PII handling, Docker Compose one-command bring-up, and the big one — deploy on self-hosted Kubernetes.

**Non-functional — how it behaves.** This is the part that actually shaped the architecture, and one constraint bent everything around it:

> **Telemetry must never slow the request it measures.**

The chat path is the product. The logging is a spectator. The moment the spectator can add latency to — or worse, fail — the chat request, you've inverted your priorities: the non-critical thing now degrades the critical thing. So observability had to be asynchronous and isolated from the request lifecycle, full stop.

The rest fell out from there. Logs must survive infrastructure failure — losing telemetry on a Redis restart is unacceptable when the product *is* the telemetry. Adding a provider must not require editing existing providers. Stored conversation data must never leak raw PII to the model, the logs, or the dashboard. And the deployment must be reproducible from files, not from my shell history.

**Scale.** I sized this honestly: a demo workload, tens to low-hundreds of events, single-region, single-tenant. That number is the difference between "Redis Streams" and "Kafka," between "one Postgres" and "a columnar store with a read replica." Designing for an imaginary million-RPS future is over-engineering wearing a nicer jacket. I designed for now and wrote down the exit ramps.

---

## Core Entities

Before APIs, the data model — because the schema is where tradeoffs get frozen into place. Five tables: `conversations`, `messages`, `inference_logs`, `provider_models`, `outbox_events`.

A few decisions I'd defend in any interview:

**UUID primary keys, minted client-side.** The SDK generates the `request_id` *before* the call fires. No database round-trip for an ID, and it's globally unique the moment ingestion scales horizontally. It's also the idempotency key, so it must exist before the first publish — which rules out a DB-generated serial.

**`started_at` vs `created_at`.** `started_at` is when the SDK fired the call; `created_at` is when ingestion wrote the row. The gap between them *is your pipeline lag*, observable for free by subtracting two columns I was already storing.

**Denormalized `total_tokens`.** Computed once on write, never recomputed. The alternative — summing tokens on every dashboard query across the whole log table — is invisible at 50 rows and a full table scan at 50 million.

**`raw_usage` and `attributes` as JSONB, beside the normalized columns.** Each provider's native usage block goes in verbatim, alongside the canonical integers. The normalized columns are what the dashboard queries; the raw blob is the escape hatch for fields I didn't model. The schema never loses fidelity and never needs a migration when a vendor invents `thoughts_token_count`.

```sql
-- the idempotency key is the whole reliability story in one constraint
CREATE TABLE inference_logs (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id   UUID UNIQUE NOT NULL,        -- minted by the SDK, dedup key
  trace_id     UUID,                         -- correlates the whole pipeline
  provider     TEXT NOT NULL, model TEXT NOT NULL,
  latency_ms   NUMERIC,
  input_tokens INT, output_tokens INT, total_tokens INT,
  cost_usd     NUMERIC(10,6),
  status       TEXT NOT NULL,
  raw_usage    JSONB, attributes JSONB,      -- provider-native, lossless
  started_at   TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT now()
);
```

The schema borrows the vocabulary of OpenTelemetry's GenAI conventions — separate `input_tokens` / `output_tokens`, a time-to-first-token field, reasoning and cache-token breakdowns. It isn't literally OTel-named (`input_tokens`, not `gen_ai.usage.input_tokens` — you don't name SQL columns after telemetry attribute keys), so wiring it to an OTel collector later is a field-mapping at the export boundary, not a redesign. The point isn't conformance; it's that the column choices came from looking at where the ecosystem already landed instead of inventing my own.

The pricing table earns its own note. Rates live in `provider_models`, not in code — a price change is a row update, not a deploy. Cost is computed at ingestion time against the current rate and frozen onto the log row, so historical costs don't rewrite themselves. And that same table is the single source of truth for the frontend's model dropdown: adding a model is one row, and the UI follows. Hardcode the list in React and you've signed up for the price table and the dropdown drifting until an invoice tells you they did.

---

## API / Interface

Small and deliberately boring, except for one streaming endpoint that isn't.

```
POST  /chat/stream                  → SSE token stream          (chatbot)
GET   /conversations                → list                       (chatbot)
GET   /conversations/{id}/messages  → resume (detokenized)       (chatbot)
PATCH /conversations/{id}/cancel    → cancel                     (chatbot)
GET   /models                       → provider→models catalog    (chatbot)
POST  /ingest                       → accept one inference log   (ingestion)
GET   /dashboard/{summary|latency|throughput|errors|cost|traces} (dashboard)
```

**SSE, not WebSockets.** Token streaming is one-directional — server pushes, client listens. WebSockets are full-duplex and bring a connection-management layer you don't need for half-duplex. SSE is plain HTTP/1.1, auto-reconnects, sails through proxies. Correct primitive beats more-powerful primitive when the extra power is unused. The stream speaks a tiny protocol: `meta` (the conversation ID), then `message` deltas, then `done`.

**`/ingest` validates at the boundary with Pydantic.** The endpoint's signature *is* its schema — a payload model with a provider whitelist and a status enum. A malformed log fails with a 422 before a line of handler code runs. The validation can't be forgotten because it isn't a step, it's the door.

The dashboard's one endpoint with teeth is latency: p50/p95 via Postgres `percentile_cont`. I wrote tests for the *shape* of those percentiles, not their values — that p95 ≥ p50 for any dataset, that both stay within [min, max] — because pinning exact numbers is brittle and proving the invariant is the thing that actually catches a broken query.

---

## Data-Flow

A single chat request, end to end, happy path:

```
1. Browser POSTs /api/chatbot/chat/stream  (one origin; nginx proxies it)
2. ChatService tokenizes the user message (PII → tokens), saves it, builds context
3. LLMWrapper.chat() picks the provider, starts timing, opens the model stream
4. Tokens stream back → SSE → browser, live
5. On completion, the wrapper publishes an InferenceEvent — fire-and-forget
6. RedisProducer XADDs to the stream   (or, on failure, INSERTs to the outbox)
7. IngestionWorker XREADGROUPs a batch, POSTs each to /ingest, XACKs on 2xx
8. Ingestion: validate → redact previews → compute cost → INSERT ON CONFLICT DO NOTHING
9. Dashboard reads inference_logs on demand
```

The property that matters is step 5 *relative to* step 4: the publish is not awaited inside the request. The user's stream has already finished before the event goes onto the bus. Telemetry is downstream of the response, never in front of it.

The failure flows are where the design earns its keep:

```
Redis down       → publish() catches XADD error → writes outbox → poller drains on recovery
Ingestion down   → events back up in stream/outbox → delivered when it returns → nothing lost
Client disconnect mid-stream → reply still persisted in the generator's finally block
Duplicate delivery → ON CONFLICT (request_id) DO NOTHING → second write is a no-op
```

Every one of those was a question I asked after the thing already "worked." Every one changed the design.

---

## HLD

Stitched together:

```
                         Browser
                            │  HTTPS
                            ▼
                    Cloudflare (edge TLS)
                            │  HTTP :80
                            ▼
                  Traefik (k3s ingress)
                            ▼
        ┌──────────── nginx (frontend pod, :30000) ───────────────────┐
        │   serves the SPA  +  reverse-proxies /api/*  (SSE-aware)     │
        └───────────────┬─────────────────────────┬───────────────────┘
              /api/chatbot                 /api/dashboard
                        ▼                         ▼
                 ChatService                 Dashboard API
                        │                  (p50/p95, cost, errors)
                  LLMWrapper                       ▲
              ┌────────┼────────┐                  │ reads
              ▼        ▼        ▼                   │
          Bedrock   Gemini    Groq                 │
              │  (anti-corruption layer)           │
              ▼                                     │
       publish(InferenceEvent)                      │
              │                                     │
      ┌───────┴────────┐                            │
      ▼                ▼                            │
 Redis Stream     outbox_events (PG)                │
      │                │ poll 10s                   │
      └───────┬────────┘                            │
              ▼                                     │
   IngestionWorker / OutboxPoller                   │
              │ POST /ingest                        │
              ▼                                      │
         Ingestion ── validate/redact/price ──► Postgres
                            ON CONFLICT       (inference_logs,
                                               messages + pii_map,
                                               provider_models)
```

Six services on one k3s node — frontend, chatbot, ingestion, dashboard, Postgres, Redis. Stateless app services scale horizontally; Redis and Postgres are pinned with persistent volumes. The frontend is the only thing the public internet touches; everything else is `ClusterIP`, reachable only through the proxy. That single-origin shape is why there's no CORS configuration anywhere in the codebase — and it was not the shape I started with.

---

## Deep Dives

Six places where the first design was wrong and I found out the hard way.

### Deep Dive 1: The event bus, and the six options I argued with myself about

The first version was the lie I told myself. Wrapper measures the call, `asyncio.create_task` fires a POST to ingestion, return. Ten lines. It worked on my laptop, with one user, with zero failures — the three conditions that make any architecture look correct.

Fire-and-forget has two holes. The task isn't awaited, so without a held reference it can be garbage-collected mid-flight. And it dies with the process — a crash between "model responded" and "log delivered" loses the event silently. You find out later, when the cost dashboard is suspiciously cheap.

I could have written "logs are best-effort" in the README and shipped. For a founding-engineer take-home that's the wrong path, because the first interview question is "what happens when ingestion is down," and "we lose data" is a bad answer about a logging product. So I made the telemetry an *event* and went shopping for a bus. Six realistic options, each rejection justified:

- **`asyncio.Queue`** — in-process, dies with the process. A variable with a `while` loop, not a bus.
- **Postgres `LISTEN/NOTIFY`** — reuses infra I have, but Postgres isn't a broker: 8KB cap, no consumer groups, and now my log table and my event bus contend on one instance.
- **Webhook to a router** — HTTP with extra steps. A single point of failure with no persistence, no replay.
- **Celery + Redis** — wrong semantics. A task queue says "do this"; an event stream says "this happened." Plus Flower, a worker, and beat as dependencies for what should be one consumer group.
- **Kafka** — correct at scale, operationally heavy, partitions and a coordinator to plan, for 50 events. The cluster overhead exists before the first message.
- **Redis Streams** — AOF persistence, real consumer groups (`XREADGROUP`, pending lists, `XACK`, `XAUTOCLAIM` to reclaim from dead workers), replay, at-least-once delivery, one container. Chosen.

Then I noticed I'd only solved the case where Redis is up.

### Deep Dive 2: The outbox nobody asked for, and "effectively-once"

Redis being down is not exotic — restart, network blip, a config typo. If it's unreachable when the call completes, the event never reaches the stream.

The textbook answer is the transactional outbox: persist the event durably to a local table, ship from there. I built a dual-path publisher that doesn't change the ingestion contract or add a separate worker process:

```python
async def publish(self, event):
    try:
        await self._redis.xadd(STREAM, event.as_fields())   # happy path
    except Exception:
        await outbox.insert(self._pool(), event)             # durable fallback
        # never re-raises: telemetry must not break the chat path
```

A poller drains `outbox_events` every 10 seconds through the *same* `deliver()` function the Redis consumer uses — one delivery path, one retry policy, not two that drift.

The part I'm proud of is that it composes with the idempotency constraint I'd already added. At-least-once delivery means an event can arrive twice — on retry, on reclaim, on a crash between POST and `XACK`. On its own that double-counts tokens and cost. The unique `request_id` with `ON CONFLICT DO NOTHING` makes the duplicate a no-op. At-least-once delivery **plus** an idempotent consumer equals **effectively-once processing** — the precise phrase, and explicitly *not* "exactly-once delivery," which is impossible in a distributed system. Saying "effectively-once processing" in an interview is the difference between someone who read the words and someone who understands them.

I tested all three failure modes against a real running stack:

```
Redis up,   ingestion up    → XADD → consumer → XACK             → 1 row
Redis down, ingestion up    → outbox INSERT → poller delivers    → 1 row
both down,  ingestion back  → outbox waits, delivers on recovery → 1 row
```

In every case the chat response streamed to completion. The product never noticed the backend was on fire.

### Deep Dive 3: PII, which got harder every time I looked at it

The spec said "PII redaction," so I did the obvious thing — a regex redactor turning emails into `[EMAIL]`, applied to the `input_preview` in the logs. Correct, and done.

Except the `messages` table stores full conversation history, raw, for resume. So I applied the same redaction there. Irreversible. Problem solved — and the product broken in the same commit.

User: "my email is foo@bar.com, remember it." Stored as "my email is [EMAIL], remember it." Next turn: "what's my email?" The model receives `[EMAIL]` as context and cannot answer. **Irreversible redaction destroys the context the conversation runs on.** V2 rejected.

V3 is reversible tokenization: replace each PII span with a stable token, keep the originals in a map, detokenize only at the display boundary.

```
user types:  "my email is foo@bar.com"
stored:      "my email is [PII:EMAIL:1]"
pii_map:     {"EMAIL:1": "foo@bar.com"}     ← JSONB on the message row

LLM sees:    "my email is [PII:EMAIL:1]"     ← never the raw value
user sees:   detokenize(stored, pii_map)     ← original, at the display route only
```

The map is a JSONB column on the message, not a side table. Atomic — it lives with the message it describes, one read, no join, no orphans. Schemaless — every message has a different shape of PII. Nullable — clean messages cost nothing, and a partial GIN index (`WHERE pii_map IS NOT NULL`) keeps audits fast without taxing the common case.

Two scars, both real. The **token format** mattered more than it had any right to. The first format was `<pii:EMAIL:1>`. Angle brackets read as XML, and a model trained on oceans of XML-structured prompts starts *echoing the tokens back* — "Understood, I'll use `<pii:EMAIL:1>` for your order." The user sees the raw token. That's worse than the bug I was fixing. Switching to `[PII:EMAIL:1]` — square brackets, uppercase — plus a system-prompt line ("opaque placeholders; refer to them by type, never echo them") fixed it. You don't learn that from a design doc. You learn it from watching a stream emit garbage.

The **title leak** I almost missed. The conversation title was `first_message[:50]` — raw PII dropped straight into metadata I wasn't tokenizing. The fix: generate the title from the *tokenized* text with a cheap, fast model after the first exchange — Groq's `llama-3.1-8b-instant`, near-free. Topic-based titles ("Order contact inquiry") instead of ones that quote the user's email back at them.

The honest caveat I keep in the docs: the `pii_map` is plaintext in Postgres. The win is **path isolation**, not encryption — raw PII never enters the logs, ingestion, the dashboard, or the provider payload. It sits in one column behind one boundary. Production wraps that in KMS. I didn't, and I say so, because the alternative is pretending.

### Deep Dive 4: Three providers, one interface, zero god-functions

Multi-provider is where codebases grow their first `if provider == "..."` ladder. I added Bedrock and Gemini for coverage and Groq because its free tier streams Llama at a few hundred tokens a second, and three latency profiles make the dashboard demo actually interesting.

Every provider speaks a different dialect for the one thing I care about:

```
Bedrock     dict access,      "input_tokens" / "output_tokens"
Gemini      attribute access, "prompt_token_count" / "candidates_token_count"
Groq        attribute access, "prompt_tokens" / "completion_tokens"
```

Different names, access patterns, and concepts — Gemini's reasoning model reports `thoughts_token_count`; nobody else has it. The answer is an anti-corruption layer: each provider file owns one `_normalize_usage` that translates its dialect into a single canonical `Usage`, and the wrapper only ever speaks `Usage`.

```python
# groq.py — the only place that knows groq's field names
def _normalize_usage(u) -> Usage:
    return Usage(input_tokens=u.prompt_tokens,
                 output_tokens=u.completion_tokens)
```

Why not one normalizer with branches? Open/Closed Principle. A central normalizer means every new provider edits shared code every other provider depends on. Per-provider, a vendor renaming a field has a blast radius of one file, and adding a provider is a new file plus one registry entry — existing providers untouched. That compounds: at ten providers the branching version is a function nobody wants to open. I added Groq third, and it touched zero lines of Bedrock or Gemini. That's the test passing.

### Deep Dive 5: Docker vs Kubernetes, and the lie about "it works locally"

The cliché is "Docker is imperative, Kubernetes is declarative," and it's not quite right — a Dockerfile is declarative, a Compose file is declarative. The real difference is *where the desired state lives*, and I learned it by violating it.

With Compose I drove the system with commands — `up`, `--build`, `down -v` — and the live state was an emergent property of my shell history. Fine, because one machine and one me. Kubernetes refuses to let you live like that. The model is a reconciliation loop: you declare the desired state in a file, and the cluster continuously drives reality toward it. You say "three replicas." One dies. You don't notice — Kubernetes noticed, fixed it, you had coffee. That sounds philosophical until it's the reason a pod crash at 2am doesn't page you.

Then I needed the public hostname live *right now* and reached for the fast thing:

```bash
kubectl create ingress inferscope -n llmobs \
  --class=traefik --rule="inferscope.atharvsingh.me/*=frontend:3000"
```

It worked in one second. And I'd just created a thing that existed **only inside the cluster** — not in the repo, not in `make deploy`, nowhere the next `kubectl apply` could see. Rebuild from the manifests and the routing is gone, with no error to explain the absence. So I converted it to a file, and *that* was the click: **declarative isn't about YAML being prettier — it's that if it isn't in a file, it doesn't exist.** The repo is the source of truth; anything imperative is a fact the next deploy is blind to.

The same lesson had already ambushed me three more times, because Minikube had hidden three gaps a real node exposes:

**Gap 1 — the browser can't reach your APIs.** chatbot and dashboard were `ClusterIP`, internal-only; the browser is outside. And the frontend had `VITE_CHATBOT_URL` baked in as an absolute URL — Vite bakes env at *build* time, so the backend address is frozen into the bundle, and on Azure that's a different machine. The fix was the nginx reverse proxy: browser hits one origin, nginx forwards `/api/chatbot/*` to `chatbot:8082` and `/api/dashboard/*` to `dashboard:8083`. Relative URLs, so the image is portable; single origin, so no CORS. The subtle trap: nginx buffers responses by default, queuing the entire SSE stream and dumping it at the end — streaming looks broken. `proxy_buffering off` plus `X-Accel-Buffering: no`, on the streaming routes specifically.

**Gap 2 — the images don't exist on the node.** Minikube worked because `eval $(minikube docker-env)` builds into its registry. k3s uses containerd; local images are invisible to it, and `imagePullPolicy: Never` with a bare tag yields `ErrImageNeverPull`. Push to Docker Hub, `imagePullPolicy: IfNotPresent`, k3s pulls and caches.

**Gap 3 — `localhost` means the container.** Obvious in hindsight, invisible locally. Inside a container, `localhost` is that container — not your laptop, where Postgres happens to run. The fix is 12-factor made literal — same image, three environments, config injected:

```
local dev      → localhost:5432
docker compose → postgres:5432       (compose service DNS)
kubernetes     → postgres:5432       (k8s service DNS, ns: llmobs)
```

ConfigMap in k8s, an `environment:` override in Compose, the local `.env` untouched. One binary, three network worlds, zero rebuilds. There was also a missing migration step — the in-cluster Postgres boots empty, nothing ran `alembic upgrade head`, and the services crash-looped on absent tables. The fix is the correct primitive: a run-once `Job` that migrates, `wait`-ed on before the app pods start. An `initContainer` would re-run on every restart; a Job runs once.

### Deep Dive 6: The deploy scars, and the password that was a real security incident

"One command" is a marketing term. The Compose file had been written but never run end-to-end — the most dangerous state for config, because it looks finished. The first `docker compose up --build`:

- **`multidict` wheel build failed.** `python:3.11-slim` has no compiler and no matching wheel, so pip tried to compile — first no `gcc`, then a `gcc` but no `stdlib.h`, because slim ships neither the compiler nor libc headers. `build-essential` fixed it.
- **`ModuleNotFoundError: No module named 'redis_bus'`.** The chatbot Dockerfile copied `sdk`, `obs`, `chatbot` — and predated the event-bus package, so it never copied `redis_bus`. The image was confidently missing a quarter of the app.
- **`cannot import name 'Mapping' from 'collections'`** — a 2019 `botocore`. `boto3` and `aioboto3` were both unpinned; latest boto3 forced a botocore the resolver couldn't reconcile, so pip backtracked `aioboto3` to an ancient version with a loose constraint that dragged in a botocore using a Python-3.10-removed API. Dropping the unused `boto3` and pinning `aioboto3` killed the spiral.

Then the OS got cute: `bind: ... forbidden by its access permissions` on port 8000. Not "in use" — *forbidden*. Windows' WinNAT had reserved 7978–8077, swallowing 8000, 8001, 8002. The app was fine; the OS had claimed the ports. Moving to 8081–8083 beat fighting WinNAT, and "works locally" silently includes "on this machine's port reservations." Azure added its own toll: B1s (1 GB) can't run this (k3s alone eats ~1 GB; the stack needs ~3), a broken `~/.ssh/config` blocked SSH until `-F NUL` ignored it, `kubectl` threw permission-denied on the root-owned kubeconfig until I exported `KUBECONFIG`, and the NodePort wouldn't open because I'd typed the port in the wrong NSG field. Each small; each only visible by doing it.

And then the one that wasn't small. A `git grep` across all history turned up the live Postgres password from the opener — `backend/.env.example`, line one, since the first commit, on a public repo. I was the one who put it there. Nobody else. The example file had a real credential because I copy-pasted my local `.env` and forgot to sanitize it. That's how it happens — not malice, not ignorance, just a copy-paste at the wrong moment that nothing flagged. `git filter-repo --replace-text` rewrites every commit to scrub the string, then a force-push overwrites public history — commits and messages preserved, every hash re-stamped because one byte changed. But the scrub is cosmetic. The thing that *actually* neutralizes a leaked secret is rotating it; once changed, the string in old history is dead text. I scrubbed and rotated, in that order of importance. If you only do one, rotate.

---

## Tradeoffs

A design without a tradeoffs section is a sales pitch, so here's the ledger — and the pattern that runs through all of it.

Redis Streams over Kafka bought persistence, consumer groups, replay, and one container; it cost a single-node memory ceiling and capped retention, and the exit ramp is Kafka the day I need several independent consumer groups or days of replay — not for fifty events. Reversible tokenization over redaction bought surviving multi-turn context, but cost the fact that the PII still exists, relocated into a JSONB column rather than deleted — risk moved, not removed, defensible only because of path isolation and only fully correct once that column is encrypted. One Postgres for chat and logs bought simplicity and a single source of truth, at the cost of an append-heavy log table sharing an instance with OLTP; the ramp is a columnar store and a read-replica-backed rollup when volume earns it. Self-hosted k3s over managed EKS/AKS bought literal compliance with the requirement and one cheap VM, and cost me ownership of the node, the upgrades, and the single point of failure. SSE over WebSockets bought the right primitive for one-directional streaming with zero connection management and cost effectively nothing at this scale. The outbox over a heavier broker bought durability with no new moving process, at the cost of a ten-second poll latency on a path that's rare by definition.

The pattern is the point: in every case the cheap, simple choice was correct *at this scale*, and every one has a marked exit for when the scale changes. "Build for now, write down the graduation path" beat "build for an imaginary later" every single time it came up — and being able to name the exit ramp is what separates a simple design from a naive one.

---

## Where it actually is

It runs at a real URL, on a real VM, behind real TLS, with 130 passing tests and a deploy reproducible from files. The cost numbers are correct because the price table and the model dropdown are the same table. The PII never reaches the model. The logging cannot take down the chat.

It also still has a placeholder Gemini rate, no dead-letter queue for permanently-rejected payloads, and a `pii_map` I'd encrypt before it went near anyone's real inbox. A senior engineer reading this repo will find those gaps in about ten minutes. The bet is that they'll also find the reasoning that put everything *else* where it is — and that on a real system, the second read is the one that matters more than the first.
