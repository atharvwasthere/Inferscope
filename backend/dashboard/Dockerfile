# Build context is the REPO ROOT (not ./backend) — the inferscope package at src/
# has to be reachable. See docker-compose.yml.
FROM python:3.11-slim

WORKDIR /app

COPY backend/dashboard/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# The inferscope package (T1). dashboard imports obs.log, which imports
# inferscope.trace — so this service needs the package even though it has no sdk/.
COPY pyproject.toml README.md /tmp/pkg/
COPY src /tmp/pkg/src
RUN pip install --no-cache-dir /tmp/pkg

COPY backend/obs /app/obs
COPY backend/dashboard /app/dashboard

ENV PYTHONPATH=/app

EXPOSE 8083
CMD ["uvicorn", "dashboard.main:app", "--host", "0.0.0.0", "--port", "8083"]
