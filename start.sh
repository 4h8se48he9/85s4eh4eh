#!/usr/bin/env bash
set -e

echo "Starting Cloudflare WARP daemon..."
warp-svc &
sleep 3

warp-cli --accept-tos registration new || true
warp-cli --accept-tos mode proxy || true
warp-cli --accept-tos proxy port 40000 || true
warp-cli --accept-tos connect || true

echo "Checking WARP SOCKS5 tunnel..."
for i in {1..15}; do
  if curl -s --socks5-hostname 127.0.0.1:40000 https://cloudflare.com/cdn-cgi/trace | grep -q "warp="; then
    echo "WARP proxy online on port 40000."
    break
  fi
  sleep 1
done

echo "Starting Gunicorn application..."
exec /opt/venv/bin/gunicorn -w 4 -b 0.0.0.0:${PORT:-8080} --timeout 120 app:app