# Build context is the REPO ROOT (not ./backend) — the inferscope package at src/
# has to be reachable. See docker-compose.yml.
FROM python:3.11-slim

WORKDIR /app

COPY backend/ingestion/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# The inferscope package (T1). Built in its own dir so the package source never
# mixes with the app root. Copied before the app code so this layer stays cached
# while service code churns.
COPY pyproject.toml README.md /tmp/pkg/
COPY src /tmp/pkg/src
RUN pip install --no-cache-dir /tmp/pkg

COPY backend/sdk /app/sdk
COPY backend/obs /app/obs
COPY backend/redis_bus /app/redis_bus
COPY backend/ingestion /app/ingestion

ENV PYTHONPATH=/app

EXPOSE 8081
CMD ["uvicorn", "ingestion.main:app", "--host", "0.0.0.0", "--port", "8081"]
