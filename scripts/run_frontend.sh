#!/usr/bin/env bash
# Start the MVP v1 frontend-lite (Vite + React) on port 5174.
# Proxies /api, /viewer, /preview to the backend on :8010 (see vite.config.ts).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$HERE/../frontend"

cd "$FRONTEND_DIR"

if [ ! -d node_modules ]; then
  echo "[run_frontend] installing deps (first run)..."
  npm install
fi

echo "[run_frontend] starting on http://127.0.0.1:5174"
exec npm run dev
