"""Structured outputs for the Qwen orchestrator.

The orchestrator returns JSON only. ANALYSIS_SCHEMA is the guided-decoding
JSON Schema handed to vLLM so the model is constrained to a parseable shape;
OrchestratorAnalysis is the validated pydantic view the backend consumes.
"""
from __future__ import annotations

from enum import Enum
from typing import List

from pydantic import BaseModel

from app.schemas.clarification import ClarificationQuestion
from app.schemas.generation import Intent

# Allowed intent strings (kept in sync with schemas.generation.Intent).
_INTENT_VALUES = [i.value for i in Intent]


class OrchestratorAnalysis(BaseModel):
    """Combined analyze + clarify + brief result from one orchestrator call."""

    intent: Intent
    confidence: float
    signals: List[str] = []
    parameters: dict = {}
    missing: List[str] = []
    questions: List[ClarificationQuestion] = []
    summary: str = ""
    assumptions: List[str] = []
    ready_to_generate: bool = False


# JSON Schema for vLLM guided_json. Mirrors OrchestratorAnalysis.
ANALYSIS_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "intent": {"type": "string", "enum": _INTENT_VALUES},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "signals": {"type": "array", "items": {"type": "string"}},
        "parameters": {"type": "object"},
        "missing": {"type": "array", "items": {"type": "string"}},
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "question": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                    "required": {"type": "boolean"},
                },
                "required": ["id", "question"],
            },
        },
        "summary": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "ready_to_generate": {"type": "boolean"},
    },
    "required": [
        "intent",
        "confidence",
        "missing",
        "questions",
        "summary",
        "ready_to_generate",
    ],
}


class RepairAction(str, Enum):
    repair = "repair"   # try another worker/repair pass with optional hint
    abort = "abort"     # stop; the failure is not worth retrying


class RepairDecision(BaseModel):
    """Orchestrator decision after a CAD export/build failure."""

    action: RepairAction
    reason: str = ""
    hint: str = ""  # extra guidance appended to the repair prompt


REPAIR_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": [a.value for a in RepairAction]},
        "reason": {"type": "string"},
        "hint": {"type": "string"},
    },
    "required": ["action", "reason"],
}


class Pipeline(str, Enum):
    mvp1_text_to_cad = "mvp1_text_to_cad"
    mvp2_robotics = "mvp2_robotics"
    mvp3_mechatronic = "mvp3_mechatronic"
    mvp4_printing = "mvp4_printing"
    clarify = "clarify"          # not ready; needs more info
    unknown = "unknown"


class NextAction(BaseModel):
    """Which pipeline/stage should run next for a project."""

    pipeline: Pipeline
    supported: bool = False         # is this pipeline active in current MVP
    needs_clarification: bool = False
    reason: str = ""


# Guided-JSON for the orchestrator decision (server fills supported/clarif).
NEXT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "pipeline": {"type": "string", "enum": [p.value for p in Pipeline]},
        "needs_clarification": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["pipeline", "reason"],
}
