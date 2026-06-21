#!/usr/bin/env bash
# Launch the full Trelis platform: Qwen orchestrator + backend (which serves the
# built frontend on a single port). One command, one URL.
set -euo pipefail

ROOT="/root/all_project_models/alfaj/text-to-cad-product"
CAD_PY="/root/anaconda3/envs/cadskills/bin"
LDP="/root/anaconda3/envs/cadskills/lib/libexpat.so.1"
mkdir -p "$ROOT/logs" "$ROOT/.run"

echo "[1/3] Qwen orchestrator (vLLM :8001) ..."
if curl -sf -m3 http://127.0.0.1:8001/v1/models >/dev/null 2>&1; then
  echo "    already up"
else
  nohup "$ROOT/scripts/serve_qwen.sh" > "$ROOT/logs/qwen.log" 2>&1 &
  echo $! > "$ROOT/.run/qwen.pid"
  echo "    starting (first run downloads weights; see logs/qwen.log)"
fi

echo "[2/3] Building frontend ..."
( cd "$ROOT/frontend" && npm run build >/dev/null 2>&1 ) && echo "    dist ready"

echo "[3/3] Backend (:8010, serves UI + API) ..."
pkill -f "uvicorn app.main:app --app-dir backend" 2>/dev/null || true
sleep 1
cd "$ROOT"
ORCHESTRATOR_ENABLED=1 \
CLAUDE_CODE_ENABLED=1 \
GENERATION_PROVIDER=qwen_claude_code \
ORCH_BASE_URL=http://127.0.0.1:8001/v1 \
ORCH_MODEL=qwen-orchestrator \
LD_PRELOAD="$LDP" \
nohup "$CAD_PY/uvicorn" app.main:app --app-dir backend --host 0.0.0.0 --port 8010 \
  > "$ROOT/logs/backend.log" 2>&1 &
echo $! > "$ROOT/.run/backend.pid"

for i in $(seq 1 20); do curl -sf -m2 http://localhost:8010/api/health >/dev/null 2>&1 && break; sleep 1; done
echo
echo "  Trelis is up:  http://localhost:8010"
echo "  Health:        curl -s http://localhost:8010/api/health"
echo "  Logs:          logs/backend.log  logs/qwen.log"
echo "  (dev hot-reload alternative: cd frontend && npm run dev -> :5174)"
