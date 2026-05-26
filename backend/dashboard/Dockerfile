FROM python:3.11-slim

WORKDIR /app

COPY dashboard/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY obs /app/obs
COPY dashboard /app/dashboard

ENV PYTHONPATH=/app

EXPOSE 8083
CMD ["uvicorn", "dashboard.main:app", "--host", "0.0.0.0", "--port", "8083"]
