"""Rule-based engineering brief generator (no LLM yet)."""
from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.generation import Brief, Intent

_INTENT_SUMMARY = {
    Intent.concept_cad: "Concept CAD part from the user's description.",
    Intent.printable_model: "3D-printable model from the user's description.",
    Intent.robotics_urdf: "Robot description (URDF) from the user's description.",
    Intent.mechatronic: "Mechatronic assembly from the user's description.",
    Intent.print_package: "Print package generation from the user's description.",
    Intent.unknown: "Intent unclear; needs clarification.",
}


def build_brief(project_id: str, prompt: str, intent: Intent, params: dict) -> Brief:
    assumptions: list[str] = []
    if not params.get("has_dimensions"):
        assumptions.append("No dimensions given; defaults will be chosen at generation.")
    if "material" not in params:
        assumptions.append("No material specified.")

    return Brief(
        project_id=project_id,
        prompt=prompt,
        intent=intent,
        summary=_INTENT_SUMMARY.get(intent, ""),
        parameters=params,
        assumptions=assumptions,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
