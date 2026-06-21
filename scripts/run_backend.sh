#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$HERE/../backend"

cd "$BACKEND_DIR"
exec uvicorn app.main:app --host 0.0.0.0 --port 8010 --reload
