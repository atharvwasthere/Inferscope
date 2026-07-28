#!/usr/bin/env bash
# Build all four images and push them to Docker Hub.
#
# Prerequisite: `docker login` as the DOCKER_USER below (or pass DOCKER_USER=...).
# The three backend services build with the REPO ROOT as context (changed in T1) — the
# inferscope package at src/ must be reachable alongside backend/. .dockerignore keeps
# .venv, node_modules and .git out of that context. The frontend builds from
# `frontend/`. Run from the repo root: bash scripts/build_push.sh
set -euo pipefail

DOCKER_USER="${DOCKER_USER:-atharv19}"
TAG="${TAG:-latest}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"

build_push() {
  local name="$1" dockerfile="$2" context="$3"
  local image="${DOCKER_USER}/inferscope-${name}:${TAG}"
  echo "==> building ${image}"
  docker build -t "${image}" -f "${dockerfile}" "${context}"
  echo "==> pushing ${image}"
  docker push "${image}"
}

# backend services: context = repo root (needs both src/ for the package and backend/)
build_push ingestion "${REPO}/backend/ingestion/Dockerfile" "${REPO}"
build_push chatbot   "${REPO}/backend/chatbot/Dockerfile"   "${REPO}"
build_push dashboard "${REPO}/backend/dashboard/Dockerfile" "${REPO}"

# frontend: context = frontend/
build_push frontend  "${REPO}/frontend/Dockerfile"          "${REPO}/frontend"

echo "==> done: pushed 4 images to ${DOCKER_USER}/inferscope-*:${TAG}"
