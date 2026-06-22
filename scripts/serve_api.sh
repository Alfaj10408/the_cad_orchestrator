#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${ADMIN_API_KEY:?set ADMIN_API_KEY}"
export GENERATION_PROVIDER="${GENERATION_PROVIDER:-qwen_claude_code}"
export CLAUDE_CODE_ENABLED="${CLAUDE_CODE_ENABLED:-1}"
export ORCH_BASE_URL="${ORCH_BASE_URL:-http://127.0.0.1:8001/v1}"
exec env LD_PRELOAD=/root/anaconda3/envs/cadskills/lib/libexpat.so.1 \
  /root/anaconda3/envs/cadskills/bin/uvicorn app.main:app \
  --app-dir backend --host 0.0.0.0 --port "${PORT:-8080}"
