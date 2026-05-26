FROM python:3.11-slim

WORKDIR /app

# build-essential (gcc + libc6-dev headers) is needed to compile the multidict/yarl
# C extensions pulled in by aioboto3 → aiohttp when no prebuilt wheel matches; slim
# ships neither a compiler nor the libc headers (stdlib.h) by default.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY chatbot/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY sdk /app/sdk
COPY obs /app/obs
COPY redis_bus /app/redis_bus
COPY alembic /app/alembic
COPY alembic.ini /app/alembic.ini
COPY chatbot /app/chatbot

ENV PYTHONPATH=/app

EXPOSE 8082
CMD ["sh", "-c", "alembic upgrade head && uvicorn chatbot.main:app --host 0.0.0.0 --port 8082"]
