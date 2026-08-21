#!/usr/bin/env bash
set -e

# Start WARP
warp-svc &
sleep 5

warp-cli --accept-tos registration new || true
warp-cli --accept-tos mode proxy || true
warp-cli --accept-tos connect || true

echo "Starting Application..."
# Use the python and gunicorn inside the venv
exec /app/venv/bin/gunicorn -w 2 -b 0.0.0.0:${PORT:-8080} app:app