#!/usr/bin/env bash
# Launch the local Qwen orchestrator as an OpenAI-compatible vLLM server.
#
# Persistent server (keep running; do not spawn per-request). Talks to the
# backend over http://127.0.0.1:8001/v1 (see backend/app/ai/llm/config.py).
#
# vLLM is already installed in the `vllmbknd` conda env (vllm 0.5.4). No pip
# install needed. First run downloads ~9.4GB AWQ weights to ~/.cache/huggingface.
#
# VRAM on the shared A100 80GB (validated 2026-06-14):
#   - AWQ 4-bit weights ~9.4GB
#   - gpu-memory-utilization 0.35 -> ~28GB reserved (weights + KV blocks)
#   - this vllm 0.5.4 build does NOT support --max-model-len, so context is the
#     model default (32768); 0.35 util gives enough KV for it. --enforce-eager
#     and --max-num-seqs bound memory/startup. Lower GPU_UTIL if VRAM is tight.
set -euo pipefail

PYTHON_BIN="${ORCH_PYTHON:-/root/anaconda3/envs/vllmbknd/bin/python}"
MODEL="${ORCH_HF_MODEL:-Qwen/Qwen2.5-14B-Instruct-AWQ}"
SERVED_NAME="${ORCH_MODEL:-qwen-orchestrator}"
PORT="${ORCH_PORT:-8001}"
GPU_UTIL="${ORCH_GPU_UTIL:-0.35}"
MAX_SEQS="${ORCH_MAX_SEQS:-8}"

exec "${PYTHON_BIN}" -m vllm.entrypoints.openai.api_server \
  --model "${MODEL}" \
  --served-model-name "${SERVED_NAME}" \
  --quantization awq \
  --gpu-memory-utilization "${GPU_UTIL}" \
  --max-num-seqs "${MAX_SEQS}" \
  --enforce-eager \
  --host 127.0.0.1 \
  --port "${PORT}"
