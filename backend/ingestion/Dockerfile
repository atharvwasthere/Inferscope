FROM python:3.11-slim

WORKDIR /app

COPY ingestion/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY sdk /app/sdk
COPY obs /app/obs
COPY ingestion /app/ingestion

ENV PYTHONPATH=/app

EXPOSE 8081
CMD ["uvicorn", "ingestion.main:app", "--host", "0.0.0.0", "--port", "8081"]
