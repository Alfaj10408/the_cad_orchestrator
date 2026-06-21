"""Rule-based clarifier (no LLM yet).

Given the prompt, intent, and extracted parameters, decide which
important info is missing and produce clarification questions.
"""
from __future__ import annotations

from app.schemas.clarification import ClarificationQuestion
from app.schemas.generation import Intent

# Questions keyed by the missing-field id.
_QUESTION_BANK: dict[str, ClarificationQuestion] = {
    "dimensions": ClarificationQuestion(
        id="dimensions",
        question="What are the approximate overall dimensions?",
        options=["50x50x50 mm", "100x100x20 mm", "let system choose"],
        required=True,
    ),
    "units": ClarificationQuestion(
        id="units",
        question="Which units should be used?",
        options=["mm", "cm", "inch"],
        required=True,
    ),
    "material": ClarificationQuestion(
        id="material",
        question="What material is the part for?",
        options=["PLA", "ABS", "PETG", "aluminum", "not sure"],
        required=False,
    ),
    "intent": ClarificationQuestion(
        id="intent",
        question="What do you want to produce?",
        options=[
            "concept CAD model",
            "3D printable model",
            "robot (URDF)",
            "mechatronic assembly",
        ],
        required=True,
    ),
}


def detect_missing(prompt: str, intent: Intent, params: dict) -> list[str]:
    """Return ids of missing important info."""
    missing: list[str] = []

    if intent == Intent.unknown:
        missing.append("intent")
        return missing

    if not params.get("has_dimensions"):
        missing.append("dimensions")
        missing.append("units")

    # Material matters for anything physically produced.
    if intent in (Intent.printable_model, Intent.mechatronic, Intent.print_package):
        if "material" not in params:
            missing.append("material")

    return missing


def build_questions(missing: list[str]) -> list[ClarificationQuestion]:
    return [_QUESTION_BANK[m] for m in missing if m in _QUESTION_BANK]


# Map free-text intent answers to Intent values.
_INTENT_ALIASES = {
    "concept cad model": Intent.concept_cad,
    "concept_cad": Intent.concept_cad,
    "3d printable model": Intent.printable_model,
    "printable_model": Intent.printable_model,
    "robot (urdf)": Intent.robotics_urdf,
    "robotics_urdf": Intent.robotics_urdf,
    "mechatronic assembly": Intent.mechatronic,
    "mechatronic": Intent.mechatronic,
    "print_package": Intent.print_package,
}


def apply_answers(intent: Intent, params: dict, answers: dict) -> tuple[Intent, dict]:
    """Fold user answers into intent + params for re-analysis."""
    params = dict(params)

    if answers.get("dimensions"):
        params["has_dimensions"] = True
        params["dimensions"] = answers["dimensions"]
    if answers.get("units"):
        params["units"] = answers["units"]
    if answers.get("material") and str(answers["material"]).lower() != "not sure":
        params["material"] = answers["material"]

    new_intent = intent
    raw = answers.get("intent")
    if raw:
        key = str(raw).strip().lower()
        if key in _INTENT_ALIASES:
            new_intent = _INTENT_ALIASES[key]
        else:
            try:
                new_intent = Intent(key)
            except ValueError:
                pass

    return new_intent, params
