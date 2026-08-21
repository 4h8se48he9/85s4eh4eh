#!/usr/bin/env bash
set -e

echo "Starting VexoStream Engine..."
exec /app/venv/bin/gunicorn -w 2 -b 0.0.0.0:${PORT:-8080} --timeout 120 app:app