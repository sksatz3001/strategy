#!/bin/sh
set -e

cd /app/dashboard
npm run start &
DASH_PID=$!

cd /app
uvicorn backend.main:app --host 0.0.0.0 --port 8000 &
API_PID=$!

cleanup() {
  kill "$API_PID" "$DASH_PID" 2>/dev/null || true
}

trap cleanup INT TERM
wait "$API_PID" "$DASH_PID"
