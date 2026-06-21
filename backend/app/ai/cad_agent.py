"""CAD worker-prompt builder.

Turns an engineering brief into the user message handed to the Claude CAD
worker. Uses the Qwen orchestrator when enabled+reachable; otherwise falls
back to a deterministic template. Returns (prompt_text, source) where source
is "orchestrator" or "deterministic".
"""
from __future__ import annotations

import re

from app.ai import orchestrator
from app.ai.llm import config as orch_config
from app.ai.llm.client import OrchestratorError

# User explicitly wants a primitive only if these words appear (and no complex
# object word). Then we DO allow a plain box/plate.
PRIMITIVE_WORDS = ("box", "cube", "plate", "block", "slab", "sheet", "cuboid")

MIN_FEATURES = 8

# Domain decompositions: object keyword -> (object_kind, named features).
_DOMAINS: dict[str, tuple[str, list[str]]] = {
    "drone": ("quadcopter drone", [
        "central fuselage body (rounded rectangular shell)",
        "four arms in X configuration",
        "four motor pods at arm ends",
        "four propellers / rotor discs",
        "landing gear legs",
        "camera gimbal mounted under the body",
        "battery bay in the lower fuselage",
        "flight-controller deck on top",
    ]),
    "quadcopter": ("quadcopter drone", []),  # alias -> filled below
    "quadrotor": ("quadcopter drone", []),
    "uav": ("quadcopter drone", []),
    "gear": ("spur gear", [
        "circular gear body / blank",
        "involute teeth around the rim",
        "central bore",
        "raised hub around the bore",
        "spokes or lightening holes",
        "keyway in the bore",
        "chamfered tooth tips",
        "filleted tooth roots",
    ]),
    "bracket": ("mounting bracket", [
        "base plate", "vertical wall", "gusset / rib reinforcement",
        "mounting holes in the base", "mounting holes in the wall",
        "counterbores for fasteners", "filleted inner corner",
        "chamfered outer edges",
    ]),
    "truck": ("toy truck", [
        "chassis frame", "cab body", "cargo bed / dump bed",
        "four wheels", "axles", "front grille / bumper",
        "headlights", "tipping hinge for the bed",
    ]),
    "car": ("toy car", [
        "chassis", "body shell", "four wheels", "axles",
        "windows / cabin", "bumpers", "headlights", "wheel arches",
    ]),
    "robot arm": ("robot arm", [
        "base", "shoulder joint link", "upper arm link", "elbow joint",
        "forearm link", "wrist joint", "gripper / end effector",
        "mounting flange",
    ]),
}
for _alias in ("quadcopter", "quadrotor", "uav"):
    _DOMAINS[_alias] = (_DOMAINS[_alias][0], _DOMAINS["drone"][1])

# Generic engineering features used to pad a complex object up to MIN_FEATURES.
_GENERIC_FILLERS = [
    "primary body / main form",
    "functional sub-assembly",
    "mounting interface / base",
    "structural reinforcement ribs",
    "fastener holes (M3/M4)",
    "fillets and chamfers on exposed edges",
    "internal cavity / weight relief",
    "recognizable surface detailing",
]


def build_feature_plan(brief: dict) -> dict:
    """Decompose the brief into named features + flags for the Claude prompt.

    Returns: object_kind, features[], complex(bool), explicit_primitive(bool),
    min_feature_count.
    """
    prompt = (brief.get("prompt", "") or "").lower()
    explicit_primitive = (
        any(w in prompt for w in PRIMITIVE_WORDS)
        and not any(k in prompt for k in _DOMAINS)
    )

    object_kind = None
    features: list[str] = []
    for key, (kind, feats) in _DOMAINS.items():
        if key in prompt:
            object_kind, features = kind, list(feats)
            break

    if explicit_primitive and not object_kind:
        # Honor a genuine primitive request — keep it simple, not "complex".
        return {
            "object_kind": "primitive block",
            "features": ["single rectangular block at the requested size"],
            "complex": False,
            "explicit_primitive": True,
            "min_feature_count": 1,
        }

    if not object_kind:
        # Generic object: infer a name, build a scaffold of features.
        object_kind = _guess_object(prompt)
        features = [f.replace("<object>", object_kind) for f in _GENERIC_FILLERS]

    # Enforce the minimum feature count for complex objects.
    i = 0
    while len(features) < MIN_FEATURES:
        filler = _GENERIC_FILLERS[i % len(_GENERIC_FILLERS)]
        if filler not in features:
            features.append(filler)
        i += 1
        if i > 50:
            break

    return {
        "object_kind": object_kind,
        "features": features,
        "complex": True,
        "explicit_primitive": False,
        "min_feature_count": MIN_FEATURES,
    }


_STOP = {"create", "make", "build", "design", "a", "an", "the", "3d", "model",
         "of", "me", "please", "printable", "cad", "generate", "with", "for"}


def _guess_object(prompt: str) -> str:
    words = [w for w in re.findall(r"[a-z]+", prompt) if w not in _STOP]
    return " ".join(words[-2:]) if words else "object"


def _deterministic_prompt(brief: dict) -> str:
    params = brief.get("parameters", {}) or {}
    answers = brief.get("user_answers", {}) or {}
    dims = params.get("dimensions") or answers.get("dimensions") or "unspecified"
    units = params.get("units") or answers.get("units") or "mm"
    material = params.get("material") or answers.get("material") or "unspecified"
    assumptions = brief.get("assumptions", []) or []
    lines = [
        "Build specification:",
        f"- Part: {brief.get('summary', '') or brief.get('prompt', '')}",
        f"- Original prompt: {brief.get('prompt', '')}",
        f"- Intent: {brief.get('intent', '')}",
        f"- Dimensions: {dims} ({units})",
        f"- Material: {material}",
    ]
    if assumptions:
        lines.append("- Assumptions: " + "; ".join(map(str, assumptions)))
    fp = build_feature_plan(brief)
    lines.append(f"- Object: {fp['object_kind']}")
    if fp["complex"]:
        lines.append("- Required named features (build ALL, as separate components where possible):")
        for i, f in enumerate(fp["features"], 1):
            lines.append(f"    {i}. {f}")
        lines.append("- Must be a recognizable, composed model — NOT a single box.")
    lines += [
        "- Coordinate convention: origin at part center, XY base plane, +Z up.",
        "- All solids closed and positive-volume, combined into one assembly Compound.",
        "",
        "Generate the build123d source now.",
    ]
    return "\n".join(lines)


def build_worker_prompt(brief: dict) -> tuple[str, str]:
    """Return (worker_prompt_text, source)."""
    if orch_config.ORCHESTRATOR_ENABLED:
        try:
            return orchestrator.worker_prompt(brief), "orchestrator"
        except OrchestratorError:
            pass
    return _deterministic_prompt(brief), "deterministic"
