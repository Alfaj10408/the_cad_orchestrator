"""High-level Qwen orchestrator (workflow planner/analyst).

Wraps the local LLM client to produce structured workflow decisions. The
orchestrator never emits CAD code; Claude remains the CAD worker.

This module is pure (no file IO); callers persist the results. On any failure
it raises OrchestratorError so callers can fall back to the deterministic
rule-based path.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import ValidationError

from app.ai.llm import config
from app.ai.llm.client import OrchestratorError, chat, chat_json
from app.core.config import PRODUCT_ROOT
from app.schemas.orchestrator import (
    ANALYSIS_SCHEMA,
    NEXT_SCHEMA,
    REPAIR_SCHEMA,
    NextAction,
    OrchestratorAnalysis,
    Pipeline,
    RepairDecision,
)

_PROMPTS_DIR = PRODUCT_ROOT / "backend" / "app" / "ai" / "prompts"


@lru_cache(maxsize=None)
def _load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / name
    text = path.read_text().strip()
    if not text:
        raise OrchestratorError(f"orchestrator prompt {name!r} is empty")
    return text


def is_enabled() -> bool:
    return config.ORCHESTRATOR_ENABLED


def analyze(prompt: str) -> OrchestratorAnalysis:
    """Combined analyze + clarify + brief in one guided-JSON call.

    Raises OrchestratorError if the server is unreachable or the response
    cannot be validated against OrchestratorAnalysis.
    """
    system = _load_prompt("orchestrator_system.txt")
    user = f"User prompt:\n{prompt.strip()}"
    data = chat_json(system, user, ANALYSIS_SCHEMA)
    try:
        result = OrchestratorAnalysis(**data)
    except ValidationError as exc:
        raise OrchestratorError(f"analysis failed schema validation: {exc}") from exc
    # Keep ready_to_generate consistent with missing-info regardless of model.
    result.ready_to_generate = len(result.missing) == 0
    return result


def worker_prompt(brief: dict) -> str:
    """Expand an engineering brief into a build spec for the Claude CAD worker.

    Returns plain-text spec (the user message Claude receives). Raises
    OrchestratorError on server failure so callers can fall back.
    """
    system = _load_prompt("worker_prompt_system.txt")
    params = brief.get("parameters", {}) or {}
    answers = brief.get("user_answers", {}) or {}
    user = (
        "Engineering brief:\n"
        f"- Prompt: {brief.get('prompt', '')}\n"
        f"- Intent: {brief.get('intent', '')}\n"
        f"- Summary: {brief.get('summary', '')}\n"
        f"- Parameters: {params}\n"
        f"- User answers: {answers}\n"
        f"- Assumptions: {brief.get('assumptions', [])}\n"
        "\nWrite the build specification now."
    )
    text = chat(system, user).strip()
    if not text:
        raise OrchestratorError("worker_prompt returned empty text")
    return text


def decide_repair(brief: dict, error: str, attempt: int, max_attempts: int) -> RepairDecision:
    """Decide whether to repair or abort after a CAD failure (guided JSON)."""
    system = _load_prompt("cad_repair_system.txt")
    user = (
        f"Engineering brief summary: {brief.get('summary', '') or brief.get('prompt', '')}\n"
        f"Intent: {brief.get('intent', '')}\n"
        f"Repair attempt {attempt + 1} of {max_attempts}.\n"
        f"CAD failure error:\n{error}\n\n"
        "Decide whether another repair pass is worthwhile."
    )
    data = chat_json(system, user, REPAIR_SCHEMA)
    try:
        return RepairDecision(**data)
    except ValidationError as exc:
        raise OrchestratorError(f"repair decision failed validation: {exc}") from exc


def inspect_report(findings: dict) -> str:
    """Summarize/interpret output findings into a short note (free text)."""
    system = _load_prompt("report_system.txt")
    user = f"Output findings (JSON):\n{findings}\n\nWrite a short review note."
    return chat(system, user).strip()


def decide_next(brief: dict) -> NextAction:
    """Route the brief to the next pipeline (guided JSON).

    Returns NextAction with pipeline + needs_clarification + reason; the caller
    fills `supported`. Raises OrchestratorError on failure.
    """
    system = _load_prompt("next_stage_system.txt")
    user = (
        f"Brief intent: {brief.get('intent', '')}\n"
        f"ready_to_generate: {brief.get('ready_to_generate', False)}\n"
        f"Summary: {brief.get('summary', '') or brief.get('prompt', '')}\n"
        f"Parameters: {brief.get('parameters', {})}\n"
        "\nDecide the next pipeline."
    )
    data = chat_json(system, user, NEXT_SCHEMA)
    try:
        return NextAction(
            pipeline=Pipeline(data["pipeline"]),
            needs_clarification=bool(data.get("needs_clarification", False)),
            reason=data.get("reason", ""),
        )
    except (KeyError, ValueError, ValidationError) as exc:
        raise OrchestratorError(f"next-stage decision invalid: {exc}") from exc
