#!/usr/bin/env bash
set -e

echo "Initializing Cloudflare WARP..."
mkdir -p /var/lib/cloudflare-warp

# Start the WARP daemon in the background
warp-svc &
sleep 5

echo "Configuring WARP Proxy..."
warp-cli --accept-tos registration new || true
warp-cli --accept-tos mode proxy || true
warp-cli --accept-tos proxy port 40000 || true
warp-cli --accept-tos connect || true

echo "Starting Gunicorn server..."
exec /app/venv/bin/gunicorn -w 2 --threads 16 -b 0.0.0.0:${PORT:-8080} --timeout 120 app:app