"""Repair/retry decision agent.

After a CAD build/export failure, decide whether to attempt another repair
pass. Uses the Qwen orchestrator when enabled+reachable; otherwise applies a
deterministic policy (repair while attempts remain, else abort).
"""
from __future__ import annotations

import os

from app.ai import orchestrator
from app.ai.llm import config as orch_config
from app.ai.llm.client import OrchestratorError
from app.schemas.orchestrator import RepairAction, RepairDecision

# Max repair passes after the first generation attempt (llm mode).
DEFAULT_MAX_REPAIRS = int(os.environ.get("MAX_CAD_REPAIRS", "2"))


def decide(brief: dict, error: str, attempt: int, max_attempts: int) -> RepairDecision:
    """Return a RepairDecision for the given failure.

    `attempt` is the number of repairs already done (0 on first failure).
    """
    if attempt >= max_attempts:
        return RepairDecision(
            action=RepairAction.abort,
            reason=f"max repair attempts ({max_attempts}) reached",
        )
    if orch_config.ORCHESTRATOR_ENABLED:
        try:
            return orchestrator.decide_repair(brief, error, attempt, max_attempts)
        except OrchestratorError:
            pass
    return RepairDecision(action=RepairAction.repair, reason="deterministic retry")
