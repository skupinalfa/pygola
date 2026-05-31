#!/usr/bin/env bash
# start_backend.sh — starts the FastAPI governance layer on port 8000.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Load .env if present
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck source=/dev/null
  source "$ROOT/.env"
  set +a
fi

# Ensure venv exists
if [ ! -f "$ROOT/.venv/bin/uvicorn" ]; then
  echo "No venv found — run ./dev.sh first to set up the environment."
  exit 1
fi

echo "Starting governance layer API on http://localhost:8000"
echo "Docs: http://localhost:8000/docs"
echo "Press Ctrl-C to stop."
echo ""

exec "$ROOT/.venv/bin/uvicorn" server.api_server:app --reload --port 8000
