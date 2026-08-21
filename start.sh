#!/usr/bin/env bash
set -e

echo "Starting Cloudflare WARP daemon..."
warp-svc &
sleep 5

warp-cli --accept-tos registration new || true
warp-cli --accept-tos mode proxy || true
warp-cli --accept-tos proxy port 40000 || true
warp-cli --accept-tos connect || true

echo "Configuring Privoxy (SOCKS5 to HTTP Bridge)..."
cat <<EOF > privoxy.config
forward-socks5t / 127.0.0.1:40000 .
listen-address 127.0.0.1:8118
keep-alive-timeout 5
socket-timeout 300
EOF
privoxy privoxy.config

echo "Starting Gunicorn server..."
exec gunicorn -w 2 -b 0.0.0.0:${PORT:-8080} --timeout 120 app:app