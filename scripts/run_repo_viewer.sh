#!/usr/bin/env bash
# Start the existing repo CAD viewer (vite dev) on a separate port.
# Does NOT rewrite the repo viewer. Keep this running while reviewing artifacts.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VIEWER_DIR="$HERE/../repo/text-to-cad/viewer"
PORT="${VIEWER_PORT:-5173}"
HOST="${VIEWER_HOST:-127.0.0.1}"

cd "$VIEWER_DIR"

if [ ! -d node_modules ]; then
  echo "[run_repo_viewer] installing deps (first run)..."
  npm install
fi

echo "[run_repo_viewer] starting CAD viewer on http://$HOST:$PORT"
echo "[run_repo_viewer] open: http://$HOST:$PORT/?dir=<project_dir>&file=cad/model.step"
exec npm run dev -- --host "$HOST" --port "$PORT"
