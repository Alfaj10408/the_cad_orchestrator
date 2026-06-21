#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$HERE/../backend"
PYTHON="${PYTHON:-/root/anaconda3/envs/cadskills/bin/python}"

cd "$BACKEND_DIR"
exec "$PYTHON" -m app.workers.runner
