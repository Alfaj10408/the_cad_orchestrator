"""Orchestrator LLM configuration (local Qwen server).

All values come from the environment so the orchestrator can be toggled
without code changes. When ORCHESTRATOR_ENABLED is false the backend uses
the existing rule-based planner/clarifier/brief path.
"""
from __future__ import annotations

import os


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


# Master switch. Off by default -> deterministic rule-based fallback.
ORCHESTRATOR_ENABLED = _flag("ORCHESTRATOR_ENABLED", "0")

# OpenAI-compatible base URL of the local vLLM Qwen server.
ORCH_BASE_URL = os.environ.get("ORCH_BASE_URL", "http://127.0.0.1:8001/v1")

# Served model id (must match --served-model-name in scripts/serve_qwen.sh).
ORCH_MODEL = os.environ.get("ORCH_MODEL", "qwen-orchestrator")

# vLLM does not require a real key; send a placeholder for the OpenAI schema.
ORCH_API_KEY = os.environ.get("ORCH_API_KEY", "not-needed")

# Network timeouts (seconds). Connect short, read longer for generation.
ORCH_CONNECT_TIMEOUT = float(os.environ.get("ORCH_CONNECT_TIMEOUT", "5"))
ORCH_READ_TIMEOUT = float(os.environ.get("ORCH_READ_TIMEOUT", "120"))

# Generation defaults. Low temp + small budgets: orchestrator emits short JSON.
ORCH_TEMPERATURE = float(os.environ.get("ORCH_TEMPERATURE", "0.2"))
ORCH_MAX_TOKENS = int(os.environ.get("ORCH_MAX_TOKENS", "1024"))

# How many times chat_json() retries on unparseable / schema-invalid output.
ORCH_JSON_RETRIES = int(os.environ.get("ORCH_JSON_RETRIES", "2"))
