#!/usr/bin/env bash
# Stop the Trelis platform (backend, Qwen). Frontend is served by the backend.
set -uo pipefail

ROOT="/root/all_project_models/alfaj/text-to-cad-product"

echo "Stopping backend ..."
pkill -f "uvicorn app.main:app --app-dir backend" 2>/dev/null && echo "  backend stopped" || echo "  backend not running"

echo "Stopping any dev frontend (vite :5174) ..."
pkill -f "vite.*5174" 2>/dev/null && echo "  vite stopped" || echo "  vite not running"

echo "Stopping Qwen orchestrator (vLLM) ..."
pkill -f "vllm.entrypoints.openai.api_server" 2>/dev/null && echo "  qwen stopped" || echo "  qwen not running"

rm -f "$ROOT/.run/"*.pid 2>/dev/null || true
echo "Done."
