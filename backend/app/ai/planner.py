"""Rule-based prompt planner (no LLM yet).

Classifies user intent and extracts simple signals from the prompt.
"""
from __future__ import annotations

import re

from app.schemas.generation import Intent

# Keyword sets per intent. Checked in priority order below.
_KEYWORDS = {
    Intent.print_package: [
        "gcode", "g-code", "g code", "bambu", "slice", "slicer",
        "print package", "print job", "nozzle", "filament",
    ],
    Intent.mechatronic: [
        "motor mount", "motor", "servo", "stepper", "battery", "pcb",
        "electronics", "screw hole", "standoff", "wiring", "enclosure for",
    ],
    Intent.robotics_urdf: [
        "robot", "urdf", "srdf", "joint", "robotic arm", "manipulator",
        "link", "actuator", "gripper", "kinematic",
    ],
    Intent.printable_model: [
        "3d print", "3d-print", "printable", "stl", "print this", "fdm",
    ],
    Intent.concept_cad: [
        "cad", "model", "design", "part", "bracket", "mount", "plate",
        "box", "enclosure", "gear", "flange", "housing", "shape", "make",
        "create", "build",
    ],
}

# Priority: most specific intent wins.
_PRIORITY = [
    Intent.print_package,
    Intent.mechatronic,
    Intent.robotics_urdf,
    Intent.printable_model,
    Intent.concept_cad,
]

_DIM_RE = re.compile(r"\d+(?:\.\d+)?\s*(mm|cm|m|in|inch|\")", re.IGNORECASE)
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_MATERIAL_RE = re.compile(
    r"\b(pla|abs|petg|tpu|nylon|resin|aluminum|aluminium|steel|brass|wood|plastic)\b",
    re.IGNORECASE,
)


def _found(prompt: str, words: list[str]) -> list[str]:
    return [w for w in words if w in prompt]


def classify(prompt: str) -> tuple[Intent, float, list[str]]:
    """Return (intent, confidence, signals)."""
    text = (prompt or "").lower().strip()
    if len(text) < 3:
        return Intent.unknown, 0.0, []

    matches: dict[Intent, list[str]] = {}
    for intent, words in _KEYWORDS.items():
        hits = _found(text, words)
        if hits:
            matches[intent] = hits

    for intent in _PRIORITY:
        if intent in matches:
            hits = matches[intent]
            # confidence scales with number of distinct keyword hits.
            confidence = min(0.5 + 0.15 * len(hits), 0.95)
            signals = [f"keyword:{h}" for h in hits]
            return intent, round(confidence, 2), signals

    return Intent.unknown, 0.2, []


def extract_parameters(prompt: str) -> dict:
    """Pull simple structured hints from the prompt."""
    text = prompt or ""
    params: dict = {}

    dims = _DIM_RE.findall(text)
    if dims:
        params["has_dimensions"] = True
        params["dimension_units"] = sorted({d.lower().replace('"', "in") for d in dims})
    else:
        params["has_dimensions"] = False

    mat = _MATERIAL_RE.findall(text)
    if mat:
        params["material"] = sorted({m.lower() for m in mat})[0]

    return params
