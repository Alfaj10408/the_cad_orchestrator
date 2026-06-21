"""Health endpoints."""
from fastapi import APIRouter

from app.ai.llm import client as orch_client
from app.ai.llm import config as orch_config
from app.core import config as app_config
from app.services import claude_code_adapter

router = APIRouter()


def _orchestrator_status() -> dict:
    if not orch_config.ORCHESTRATOR_ENABLED:
        return {"enabled": False, "ok": False, "detail": "ORCHESTRATOR_ENABLED is off"}
    status = orch_client.health()
    return {
        "enabled": True,
        "ok": status["ok"],
        "detail": status["detail"],
        "model": status["model"],
        "base_url": orch_config.ORCH_BASE_URL,
    }


@router.get("/health")
def health() -> dict:
    """Aggregate component health so the frontend can show distinct badges."""
    orch = _orchestrator_status()
    cc = claude_code_adapter.health()
    return {
        "status": "ok",
        "generation_provider": app_config.GENERATION_PROVIDER,
        "components": {
            "backend": {"ok": True},
            "qwen_planner": {"enabled": orch["enabled"], "ok": orch["ok"]},
            "claude_code": {
                "enabled": cc.get("enabled", False),
                "ok": bool(cc.get("authenticated")),
            },
            "cad_worker": {"ok": True},
            "viewer": {"base_url": app_config.VIEWER_BASE_URL},
        },
    }


@router.get("/health/orchestrator")
def orchestrator_health() -> dict:
    """Liveness of the local Qwen orchestrator server."""
    return _orchestrator_status()


@router.get("/health/claude-code")
def claude_code_health() -> dict:
    """Claude Code CLI install/auth status (no credentials exposed)."""
    return claude_code_adapter.health()
