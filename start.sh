#!/usr/bin/env bash
set -e

# Use Render assigned PORT or default to 8501
PORT="${PORT:-8501}"

echo "Starting Streamlit UI on port $PORT..."
exec streamlit run app.py \
    --server.port "$PORT" \
    --server.address 0.0.0.0 \
    --server.headless true \
    --server.enableCORS false \
    --server.enableXsrfProtection false
