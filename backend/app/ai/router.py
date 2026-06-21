"""Next-stage / pipeline router.

Maps an engineering brief to the pipeline that should run next. Deterministic
intent->pipeline mapping by default; orchestrator override when enabled. Only
MVP v1 (text-to-cad) is active per the project roadmap; other pipelines route
but are marked unsupported.
"""
from __future__ import annotations

from app.ai import orchestrator
from app.ai.llm import config as orch_config
from app.ai.llm.client import OrchestratorError
from app.schemas.generation import Intent
from app.schemas.orchestrator import NextAction, Pipeline

# Active pipelines in the current MVP. Keep in sync with the roadmap.
ACTIVE_PIPELINES = {Pipeline.mvp1_text_to_cad}

_INTENT_TO_PIPELINE = {
    Intent.concept_cad: Pipeline.mvp1_text_to_cad,
    Intent.printable_model: Pipeline.mvp1_text_to_cad,
    Intent.robotics_urdf: Pipeline.mvp2_robotics,
    Intent.mechatronic: Pipeline.mvp3_mechatronic,
    Intent.print_package: Pipeline.mvp4_printing,
    Intent.unknown: Pipeline.clarify,
}


def _deterministic(brief: dict) -> NextAction:
    if not brief.get("ready_to_generate", False):
        return NextAction(
            pipeline=Pipeline.clarify,
            needs_clarification=True,
            reason="brief not ready; clarification required",
        )
    try:
        intent = Intent(brief.get("intent", "unknown"))
    except ValueError:
        intent = Intent.unknown
    pipeline = _INTENT_TO_PIPELINE.get(intent, Pipeline.clarify)
    return NextAction(
        pipeline=pipeline,
        needs_clarification=pipeline == Pipeline.clarify,
        reason=f"routed from intent {intent.value}",
    )


def decide_next(brief: dict) -> NextAction:
    """Return the next pipeline for the brief, with `supported` filled in."""
    action: NextAction
    if orch_config.ORCHESTRATOR_ENABLED:
        try:
            action = orchestrator.decide_next(brief)
        except OrchestratorError:
            action = _deterministic(brief)
    else:
        action = _deterministic(brief)

    action.supported = action.pipeline in ACTIVE_PIPELINES
    return action
