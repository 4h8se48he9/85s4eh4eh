#!/usr/bin/env bash
set -e

echo "Fetching wgcf (WARP config generator)..."
if [ ! -f "wgcf" ]; then
    curl -sSL -o wgcf https://github.com/ViRb3/wgcf/releases/download/v2.2.22/wgcf_2.2.22_linux_amd64
    chmod +x wgcf
fi

echo "Fetching wireproxy (User-space WireGuard client)..."
if [ ! -f "wireproxy" ]; then
    curl -sSL -o wireproxy.tar.gz https://github.com/octeep/wireproxy/releases/download/v1.0.8/wireproxy_linux_amd64.tar.gz
    tar -xzf wireproxy.tar.gz
    chmod +x wireproxy
fi

if [ ! -f "wgcf-profile.conf" ]; then
    echo "Registering Cloudflare WARP account..."
    ./wgcf register --accept-tos || true
    ./wgcf generate || true
fi

if [ -f "wgcf-profile.conf" ]; then
    echo "Translating WARP profile to Wireproxy format..."
    WG_PRIV=$(grep "PrivateKey" wgcf-profile.conf | awk -F ' = ' '{print $2}' | tr -d '\r')
    WG_ADDR=$(grep "Address" wgcf-profile.conf | head -n 1 | awk -F ' = ' '{print $2}' | tr -d '\r')
    WG_PUB=$(grep "PublicKey" wgcf-profile.conf | awk -F ' = ' '{print $2}' | tr -d '\r')

    cat <<EOF > wireproxy.conf
[Interface]
PrivateKey = ${WG_PRIV}
Address = ${WG_ADDR}
MTU = 1280

[Peer]
PublicKey = ${WG_PUB}
Endpoint = engage.cloudflareclient.com:2408

[Socks5]
BindAddress = 127.0.0.1:40000
EOF

    echo "Starting User-Space WARP SOCKS5 Tunnel..."
    ./wireproxy -c wireproxy.conf &
    sleep 3
else
    echo "WARNING: WARP generation failed. Proceeding without proxy..."
fi

echo "Starting Gunicorn server..."
exec /app/venv/bin/gunicorn -w 2 --threads 16 -b 0.0.0.0:${PORT:-8080} --timeout 120 app:app