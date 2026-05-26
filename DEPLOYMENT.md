# Deployment

[← back to README](./README.md)

Two ways to run inferscope:

- **Docker Compose** — local dev, one command, everything on your laptop.
- **Kubernetes on a self-hosted k3s cluster (Azure VM)** — the production-shaped target.

Both run the same images; they differ only in how networking, scaling, and config are wired.

---

## 1. Docker Compose (local)

**Prerequisites:** Docker + Docker Compose, and `backend/.env` filled from `backend/.env.example`
(real `AWS_*`, `GOOGLE_API_KEY`, `GROQ_API_KEY`).

**One command:**
```bash
docker compose up --build
# UI → http://localhost:3000
```

**How config works.** Each backend service loads `env_file: backend/.env`, then `docker-compose.yml`
applies an `environment:` block that **overrides** the hostname-bearing vars (`DATABASE_URL`,
`REDIS_URL`, `INGESTION_URL`). Compose precedence is `environment:` > `env_file:`, so your local
`.env` (which points at `localhost` for non-docker runs) is left untouched while the containers get
the right **service names** (`postgres`, `redis`, `ingestion`). Inside a container `localhost` is the
container itself — using it for the DB/Redis is the classic mistake this override prevents.

The chatbot image runs `alembic upgrade head` on startup (see `backend/chatbot/Dockerfile`), so the
schema is created automatically the first time you `up`.

---

## 2. Why Kubernetes over Compose

Compose is great for one box. Kubernetes earns its keep when you want production behaviour:

- **Self-healing** — a crashed pod is restarted; a failed readiness probe is pulled from rotation.
- **Rolling updates** — new image rolls out pod-by-pod with health gates, no downtime.
- **Declarative config** — desired state in YAML; the cluster reconciles to it.
- **Horizontal scale** — `replicas: N`, and the Redis Streams consumer group fans work across them.

inferscope follows the **12-factor app** config principle: *config lives in the environment, not the
code or image*. The exact same image runs in all three network worlds — only the injected env differs:

| World | `DATABASE_URL` host |
|---|---|
| Local (non-docker) | `localhost:5432` |
| Docker Compose | `postgres:5432` (compose service DNS) |
| Kubernetes | `postgres:5432` (k8s Service DNS, namespace `llmobs`) |

One image, three environments, zero rebuilds — that's the 12-factor payoff.

---

## 3. Kubernetes setup (Azure VM + k3s)

**k3s** is lightweight, certified Kubernetes in a single binary — a real, self-hosted cluster on one
VM. Your `k8s/*.yaml` manifests apply to it unchanged.

### Phase 1 — build & push images (your machine, has Docker)
```bash
docker login                       # as atharv19
make push                          # runs scripts/build_push.sh → 4 images to Docker Hub
# no make installed (e.g. Windows)? run the script directly:
#   DOCKER_USER=atharv19 bash scripts/build_push.sh
```
Images publish as `atharv19/inferscope-{ingestion,chatbot,dashboard,frontend}:latest`.
Make the Docker Hub repos **public** so k3s pulls them with no pull secret.

### Phase 2 — create the Azure VM
Portal → **Virtual machines → Create → Azure virtual machine**:
- **Image:** Ubuntu Server 24.04 LTS · **Size:** B2ms (2 vCPU / 8 GB) · **Auth:** SSH public key.
- **Networking:** allow inbound **SSH (22)** (restrict Source to your IP).
- Create, then pin the **Public IP** to *Static* (VM → Networking → Public IP).

### Phase 3 — install k3s (on the VM)
```bash
ssh azureuser@<PUBLIC_IP>
curl -sfL https://get.k3s.io | sh -
mkdir -p ~/.kube && sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config && sudo chown $USER ~/.kube/config
kubectl get nodes                  # Ready = cluster up
```

### Phase 4 — deploy
Copy `k8s/` to the VM (git clone or scp), fill the real keys in `k8s/secret.yaml`, then:
```bash
make deploy
```
`deploy` applies in dependency order: namespace → configmap → secret → postgres (wait) →
redis (wait) → **migrate Job (wait for completion)** → ingestion → chatbot → dashboard → frontend.

### Phase 5 — open the port
Azure portal → VM → **Networking → Add inbound port rule**: destination port **30000**, protocol
TCP, action Allow.

### Phase 6 — verify
```bash
kubectl get all -n llmobs          # migrate Job Complete; all deploys 1/1 Available
# browse → http://<PUBLIC_IP>:30000
```

---

## 4. Five deployment problems solved

The manifests started Minikube-shaped. Five gaps had to close before a browser-facing cloud deploy
would work. For each: what breaks, why the fix is right, what was rejected.

### Problem 1 — browser → API routing
**Breaks:** `chatbot` and `dashboard` are `ClusterIP` (internal only), but the browser calls them
directly. The browser sits outside the cluster and can't resolve `chatbot:8082`. Chat does nothing.
**Fix:** the frontend's nginx (`frontend/nginx.conf`) reverse-proxies `/api/chatbot/*` → `chatbot:8082`
and `/api/dashboard/*` → `dashboard:8083`. The browser only ever talks to the frontend origin.
**Rejected:** `NodePort` on each backend — that means 3 exposed ports, the public IP baked into the
frontend build, and CORS across 3 origins. The proxy avoids all three. (See §5.)

### Problem 2 — image registry
**Breaks:** `imagePullPolicy: Never` + bare image names only work on Minikube (its docker daemon held
the images). k3s uses **containerd**; those images don't exist there → `ErrImageNeverPull`.
**Fix:** push to **Docker Hub** and set `imagePullPolicy: IfNotPresent`; k3s pulls by reference.
**Rejected:** `k3s ctr images import` of a tarball per VM — manual, fragile, doesn't scale to >1 node.

### Problem 3 — schema migrations
**Breaks:** in-cluster postgres boots empty. `ingestion`/`dashboard` have no migration step → they
query missing tables → crash-loop.
**Fix:** `k8s/migrate-job.yaml` — a run-once **Job** (the correct k8s primitive for one-off work) that
runs `alembic upgrade head` using the chatbot image (which already ships alembic + the migrations +
`psycopg2-binary`). `deploy` waits for it before starting apps.
**Rejected:** an `initContainer` on every app — it would re-run on each pod restart/scale; a Job runs once.
(The chatbot's own startup `alembic upgrade head` stays — it's what gives Compose its schema, and it's
a harmless no-op in k8s once the Job has run.)

### Problem 4 — missing secret
**Breaks:** `GROQ_API_KEY` was absent from `k8s/secret.yaml`. Groq calls 401 — and because session
titles are generated by a Groq model, **title generation breaks too**. Bigger blast radius than "one
provider down."
**Fix:** add `GROQ_API_KEY` to the Secret (alongside `AWS_*` + `GOOGLE_API_KEY`).
**Rejected:** putting it in the ConfigMap — keys are secrets, not config; they belong in the Secret.

### Problem 5 — localhost vs service names
**Breaks:** `DATABASE_URL`/`REDIS_URL` pointing at `localhost` resolve to the container itself, not
the data services → connection refused.
**Fix:** inject service-DNS hostnames per environment — `environment:` overrides in Compose, the
ConfigMap in k8s. The image never hardcodes a host (12-factor).
**Rejected:** editing `.env` to use service names — that would break non-docker local runs. Override at
the orchestration layer instead.

---

## 5. The nginx reverse-proxy decision

Why one proxy instead of exposing each backend:

- **Single origin** — the browser only calls the frontend, so there is **no CORS** to configure.
- **Relative URLs** — the frontend fetches `/api/chatbot/...`, never an absolute host. The built image
  is **portable**: the same bundle runs on any IP/domain with no rebuild. (Vite bakes env at *build*
  time, so a baked-in backend URL would mean rebuilding per environment — this sidesteps that entirely.)
- **SSE needs `proxy_buffering off`** — by default nginx buffers the upstream response, so streamed
  tokens would queue and arrive all at once at the end. Turning buffering off (plus `X-Accel-Buffering:
  no`) lets tokens flow to the browser as they're produced.

In dev there is no nginx, so `vite.config.js` proxies the same `/api/*` prefixes to `localhost:8082/8083`
— identical frontend code in dev and prod.

---

## 6. Production graduation path

What would change moving from this demo to real production:

- **NodePort → Ingress + TLS** — an ingress controller (k3s ships Traefik) with cert-manager for HTTPS,
  instead of a raw `:30000` NodePort.
- **Single-node k3s → multi-node** — add agent nodes; bump `replicas`; the consumer group already fans out.
- **Docker Hub → private registry** — ACR/ECR/Harbor with an `imagePullSecret`.
- **Plain Secret → Sealed Secrets / Vault** — `secret.yaml` is base64, not encrypted; never commit real
  values. Use Bitnami Sealed Secrets or an external secrets operator backed by a vault.
- **Manual deploy → CI/CD** — GitHub Actions builds + pushes images and `kubectl apply`s on merge.
- **Single postgres → read replica** — point dashboard reads at a replica so analytics scans don't
  block ingestion writes (see [docs/SCHEMA.md](./docs/SCHEMA.md)).
