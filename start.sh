#!/usr/bin/env bash
set -e

echo "Starting Cloudflare WARP daemon..."
warp-svc &
sleep 5

warp-cli --accept-tos registration new || true
warp-cli --accept-tos mode proxy || true
warp-cli --accept-tos connect || true

echo "Starting Gunicorn server..."
exec gunicorn -w 2 -b 0.0.0.0:${PORT:-8080} app:app