#!/usr/bin/env bash
# One command to run the whole stack: API server + static frontend + browser.
# Ctrl-C stops both.
set -e
cd "$(dirname "$0")"

API_PORT=8420
WEB_PORT=5500

echo "Starting API server on :$API_PORT ..."
uv run server.py --port "$API_PORT" &
API_PID=$!

cleanup() { echo -e "\nStopping..."; kill "$API_PID" "$WEB_PID" 2>/dev/null; }
trap cleanup EXIT

sleep 1.5

echo "Serving frontend on :$WEB_PORT ..."
(cd frontend && python3 -m http.server "$WEB_PORT" >/dev/null 2>&1) &
WEB_PID=$!

sleep 1
open "http://localhost:$WEB_PORT" 2>/dev/null || echo "Open http://localhost:$WEB_PORT manually"

wait
