"""Application configuration."""
from __future__ import annotations

import os
from pathlib import Path

# .../backend/app/core/config.py -> parents[3] = product root
PRODUCT_ROOT = Path(__file__).resolve().parents[3]

STORAGE_ROOT = Path(os.environ.get("STORAGE_ROOT", PRODUCT_ROOT / "storage"))
PROJECTS_ROOT = STORAGE_ROOT / "projects"

API_PREFIX = "/api"
APP_TITLE = "text-to-cad backend"
APP_VERSION = "0.1.0"

# Base URL of the running repo CAD viewer (vite dev/serve).
VIEWER_BASE_URL = os.environ.get("VIEWER_BASE_URL", "http://localhost:5173")

# Standard subfolders created inside each project.
PROJECT_SUBDIRS = (
    "source",
    "cad",
    "meshes",
    "robot",
    "print",
    "reports",
    "package",
)


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


# ---------- Generation provider selection ----------
# Modes: deterministic | qwen_claude_code | anthropic_api
GENERATION_PROVIDER = os.environ.get("GENERATION_PROVIDER", "deterministic")

# ---------- Claude Code CLI (subscription, no API key) ----------
CLAUDE_CODE_ENABLED = _flag("CLAUDE_CODE_ENABLED", "1")
CLAUDE_CODE_BINARY = os.environ.get("CLAUDE_CODE_BINARY", "/root/.local/bin/claude")
CLAUDE_CODE_MODEL = os.environ.get("CLAUDE_CODE_MODEL", "sonnet")
CLAUDE_CODE_MAX_TURNS = int(os.environ.get("CLAUDE_CODE_MAX_TURNS", "15"))
CLAUDE_CODE_TIMEOUT_SECONDS = int(os.environ.get("CLAUDE_CODE_TIMEOUT_SECONDS", "900"))
CLAUDE_CODE_PERMISSION_MODE = os.environ.get("CLAUDE_CODE_PERMISSION_MODE", "acceptEdits")
# Tools Claude may use inside its sandboxed workspace (fixed; never from browser).
CLAUDE_CODE_TOOLS = os.environ.get("CLAUDE_CODE_TOOLS", "Read,Write,Edit,Bash")
# Component-generation overrides (turn robustness). No Bash: backend owns
# STEP export/inspection, so component calls only Read/Write/Edit.
CLAUDE_CODE_COMPONENT_TOOLS = os.environ.get("CLAUDE_CODE_COMPONENT_TOOLS", "Read,Write,Edit")
CLAUDE_CODE_COMPONENT_MAX_TURNS = int(os.environ.get("CLAUDE_CODE_COMPONENT_MAX_TURNS", "12"))
CLAUDE_CODE_COMPONENT_NEAR_CAP = int(os.environ.get("CLAUDE_CODE_COMPONENT_NEAR_CAP", "8"))
# Workspace root for per-job Claude runs.
CLAUDE_CODE_WORKSPACE_ROOT = Path(
    os.environ.get("CLAUDE_CODE_WORKSPACE_ROOT", PRODUCT_ROOT / "runs")
)
# Max concurrent Claude processes (default 1).
CLAUDE_CODE_MAX_CONCURRENT = int(os.environ.get("CLAUDE_CODE_MAX_CONCURRENT", "1"))
# Safety limits.
CLAUDE_CODE_MAX_PROMPT_CHARS = int(os.environ.get("CLAUDE_CODE_MAX_PROMPT_CHARS", "20000"))
CLAUDE_CODE_MAX_OUTPUT_BYTES = int(
    os.environ.get("CLAUDE_CODE_MAX_OUTPUT_BYTES", str(50 * 1024 * 1024))
)
# Max Claude repair attempts after a failed CAD execution.
CLAUDE_CODE_MAX_REPAIRS = int(os.environ.get("CLAUDE_CODE_MAX_REPAIRS", "2"))

# ---------- /v1 production API ----------
API_DB_PATH = os.environ.get("API_DB_PATH", str(STORAGE_ROOT / "api.db"))
API_DB_BUSY_TIMEOUT_MS = int(os.environ.get("API_DB_BUSY_TIMEOUT_MS", "5000"))
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")
API_KEY_SALT = os.environ.get("API_KEY_SALT", "dev-salt-change-me")
API_MAX_QUEUE_DEPTH = int(os.environ.get("API_MAX_QUEUE_DEPTH", "32"))
JOB_WALLCLOCK_TIMEOUT = int(os.environ.get("JOB_WALLCLOCK_TIMEOUT", "5400"))
V1_CORS_ORIGINS = [o for o in os.environ.get("V1_CORS_ORIGINS", "").split(",") if o]
API_MIN_DISK_MB = int(os.environ.get("API_MIN_DISK_MB", "500"))
API_READYZ_CACHE_S = int(os.environ.get("API_READYZ_CACHE_S", "15"))
API_QUOTA_ENABLED = _flag("API_QUOTA_ENABLED", "1")
API_DEFAULT_DAILY_JOB_LIMIT = int(os.environ.get("API_DEFAULT_DAILY_JOB_LIMIT", "50"))
API_DEFAULT_MAX_IN_FLIGHT = int(os.environ.get("API_DEFAULT_MAX_IN_FLIGHT", "3"))

# --- /v1 rate limiting (P2) ---
API_RATE_LIMIT_ENABLED = _flag("API_RATE_LIMIT_ENABLED", "1")
API_RATE_SUBMIT_PER_MIN = int(os.environ.get("API_RATE_SUBMIT_PER_MIN", "10"))
API_RATE_READ_PER_MIN = int(os.environ.get("API_RATE_READ_PER_MIN", "120"))
API_RATE_SSE_PER_MIN = int(os.environ.get("API_RATE_SSE_PER_MIN", "30"))
API_RATE_ADMIN_PER_MIN = int(os.environ.get("API_RATE_ADMIN_PER_MIN", "60"))
API_RATE_MAX_BUCKETS = int(os.environ.get("API_RATE_MAX_BUCKETS", "10000"))

# --- /v1 artifact retention (P2) ---
API_RETENTION_ENABLED = _flag("API_RETENTION_ENABLED", "1")
API_RETENTION_COMPLETED_DAYS = int(os.environ.get("API_RETENTION_COMPLETED_DAYS", "7"))
API_RETENTION_FAILED_DAYS = int(os.environ.get("API_RETENTION_FAILED_DAYS", "3"))
API_RETENTION_CANCELLED_DAYS = int(os.environ.get("API_RETENTION_CANCELLED_DAYS", "1"))
API_RETENTION_MIN_AGE_S = int(os.environ.get("API_RETENTION_MIN_AGE_S", "3600"))
API_RETENTION_SWEEP_INTERVAL_S = int(os.environ.get("API_RETENTION_SWEEP_INTERVAL_S", "3600"))
API_RETENTION_MAX_DELETE = int(os.environ.get("API_RETENTION_MAX_DELETE", "1000"))
