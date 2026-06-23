#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${ADMIN_API_KEY:=}"
: "${ADMIN_API_KEYS:=}"
if [ -z "$ADMIN_API_KEY" ] && [ -z "$ADMIN_API_KEYS" ]; then
  echo "set ADMIN_API_KEY or ADMIN_API_KEYS" >&2; exit 1
fi
: "${API_KEY_SALT:?set API_KEY_SALT (non-default) for production}"
export GENERATION_PROVIDER="${GENERATION_PROVIDER:-qwen_claude_code}"
export CLAUDE_CODE_ENABLED="${CLAUDE_CODE_ENABLED:-1}"
export ORCH_BASE_URL="${ORCH_BASE_URL:-http://127.0.0.1:8001/v1}"
# WORKERS=1 (default) reproduces current single-process behavior. N>1 forks N
# uvicorn processes sharing one SQLite DB; only meaningful with API_WORKER_MODE=claim.
# Effective concurrency = WORKERS * CLAUDE_CODE_MAX_CONCURRENT — keep <= the Claude
# subscription session limit. See docs/MULTIWORKER_ACTIVATION_RUNBOOK.md.
export WORKERS="${WORKERS:-1}"
exec env LD_PRELOAD=/root/anaconda3/envs/cadskills/lib/libexpat.so.1 \
  /root/anaconda3/envs/cadskills/bin/uvicorn app.main:app \
  --app-dir backend --host 0.0.0.0 --port "${PORT:-8080}" --workers "${WORKERS}"
