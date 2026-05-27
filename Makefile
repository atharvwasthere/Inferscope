.PHONY: up down logs reset ps build push deploy undeploy k8s-status

# ---- Docker Compose (local) ----

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f

reset:
	docker compose down -v

ps:
	docker compose ps

build:
	docker compose build

# ---- Kubernetes (k3s on a VM) ----

# Build all four images and push to Docker Hub (run on a machine with docker + `docker login`).
push:
	bash scripts/build_push.sh

# Apply manifests in dependency order: namespace → config → data → migrate → services.
# Images are pulled from Docker Hub (run `make push` first). Run this where kubectl
# points at the k3s cluster.
deploy:
	kubectl apply -f k8s/namespace.yaml
	kubectl apply -f k8s/configmap.yaml
	kubectl apply -f k8s/secret.yaml
	kubectl apply -f k8s/postgres/
	kubectl rollout status -n llmobs deploy/postgres
	kubectl apply -f k8s/redis/
	kubectl rollout status -n llmobs deploy/redis
	kubectl apply -f k8s/migrate-job.yaml
	kubectl wait --for=condition=complete job/migrate -n llmobs --timeout=180s
	kubectl apply -f k8s/ingestion/
	kubectl apply -f k8s/chatbot/
	kubectl apply -f k8s/dashboard/
	kubectl apply -f k8s/frontend/
	kubectl apply -f k8s/ingress.yaml

undeploy:
	kubectl delete namespace llmobs

k8s-status:
	kubectl get all -n llmobs
