# Build context is the REPO ROOT (not ./backend) — the inferscope package at src/
# has to be reachable. See docker-compose.yml.
FROM python:3.11-slim

WORKDIR /app

# build-essential (gcc + libc6-dev headers) is needed to compile the multidict/yarl
# C extensions pulled in by aioboto3 → aiohttp when no prebuilt wheel matches; slim
# ships neither a compiler nor the libc headers (stdlib.h) by default.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY backend/chatbot/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# The inferscope package (T1). Built in its own dir so the package source never
# mixes with the app root. Copied before the app code so this layer stays cached
# while service code churns.
COPY pyproject.toml README.md /tmp/pkg/
COPY src /tmp/pkg/src
RUN pip install --no-cache-dir /tmp/pkg

COPY backend/sdk /app/sdk
COPY backend/obs /app/obs
COPY backend/alembic /app/alembic
COPY backend/alembic.ini /app/alembic.ini
COPY backend/chatbot /app/chatbot

ENV PYTHONPATH=/app

EXPOSE 8082
CMD ["sh", "-c", "alembic upgrade head && uvicorn chatbot.main:app --host 0.0.0.0 --port 8082"]
