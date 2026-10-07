#!/bin/bash
set -e

# Start Arq worker in the background
echo "Starting Arq worker process..."
arq app.worker.WorkerSettings &

# Start Uvicorn web server in the foreground
echo "Starting FastAPI server on port ${PORT:-8000}..."
exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips="*"