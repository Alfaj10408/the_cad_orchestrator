"""Qwen-as-project-manager: engineering spec + component decomposition.

Turns a brief + repo-skill work order into a design_spec and a normalized
component_manifest so each component is generated as its own small Claude task
instead of one giant assembly generation. Writes reports/design_spec.json and
reports/component_manifest.json.
"""
from __future__ import annotations

import json
import re

from app.ai import cad_agent, work_order
from app.core import paths

_DIM_RE = re.compile(r"\d+(?:\.\d+)?")


def _overall_dims(dim_text: str) -> tuple[float, float, float]:
    nums = [float(n) for n in _DIM_RE.findall(dim_text or "")]
    if len(nums) >= 3:
        return nums[0], nums[1], nums[2]
    if len(nums) == 1:
        return nums[0], nums[0], nums[0]
    return 200.0, 200.0, 120.0


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s[:40] or "part"


# Canonical component manifests for known assemblies (name, quantity, role).
_DRONE_COMPONENTS = [
    ("fuselage", 1, "central body shell housing electronics"),
    ("arm", 4, "boom arm from fuselage to a motor pod (X configuration)"),
    ("motor_pod", 4, "motor mount cylinder at the end of each arm"),
    ("propeller", 4, "two-blade rotor disc on each motor pod"),
    ("landing_gear", 1, "leg set under the fuselage for ground clearance"),
    ("camera_mount", 1, "gimbal bracket mounted under the nose"),
    ("battery_bay", 1, "battery tray cavity in the lower fuselage"),
    ("controller_deck", 1, "flat deck on top of the fuselage for the flight controller"),
]

_DOMAIN_MANIFESTS = {
    "quadcopter drone": _DRONE_COMPONENTS,
}


def _component_bbox(name: str, env: tuple[float, float, float]) -> dict:
    x, y, z = env
    # Loose per-component target envelopes (mm), used only as soft validation hints.
    frac = {
        "fuselage": (0.45, 0.45, 0.5),
        "arm": (0.5, 0.08, 0.08),
        "motor_pod": (0.12, 0.12, 0.18),
        "propeller": (0.4, 0.05, 0.03),
        "landing_gear": (0.5, 0.5, 0.35),
        "camera_mount": (0.15, 0.15, 0.15),
        "battery_bay": (0.35, 0.25, 0.18),
        "controller_deck": (0.35, 0.35, 0.06),
    }.get(name, (0.4, 0.4, 0.4))
    return {"x": round(x * frac[0], 1), "y": round(y * frac[1], 1), "z": round(z * frac[2], 1)}


def build_design_spec(project_id: str, brief: dict, wo: dict) -> dict:
    """Engineering specification (project-manager view). Persisted to reports/."""
    fp = cad_agent.build_feature_plan(brief)
    env = _overall_dims(wo.get("dimensions", ""))
    spec = {
        "project_id": project_id,
        "user_prompt": wo.get("user_prompt", ""),
        "object_class": wo.get("object_class", fp["object_kind"]),
        "object_kind": fp["object_kind"],
        "intent": wo.get("intent", ""),
        "complex": fp["complex"],
        "explicit_primitive": fp["explicit_primitive"],
        "overall_envelope_mm": {"x": env[0], "y": env[1], "z": env[2]},
        "units": wo.get("units", "mm"),
        "material": wo.get("material", "PLA"),
        "coordinate_convention": wo.get("coordinate_convention", ""),
        "expected_geometry": wo.get("expected_geometry", {}),
        "validation_plan": wo.get("validation_plan", {}),
        "future_pipeline": wo.get("future_pipeline"),
    }
    reports = paths.project_dir(project_id) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "design_spec.json").write_text(json.dumps(spec, indent=2))
    return spec


def build_component_manifest(project_id: str, brief: dict, design_spec: dict) -> dict:
    """Normalized per-component generation tasks. Persisted to reports/."""
    fp = cad_agent.build_feature_plan(brief)
    env = (
        design_spec["overall_envelope_mm"]["x"],
        design_spec["overall_envelope_mm"]["y"],
        design_spec["overall_envelope_mm"]["z"],
    )

    raw = _DOMAIN_MANIFESTS.get(design_spec["object_kind"])
    if raw is None:
        # Generic complex object: slugify the planned features into components.
        raw = [(_slug(f), 1, f) for f in fp["features"][:8]]

    components = []
    for name, qty, role in raw:
        components.append({
            "name": name,
            "quantity": qty,
            "role": role,
            "target_bbox_mm": _component_bbox(name, env),
            "source": f"output/components/{name}/generate.py",
            "step": f"output/components/{name}/{name}.step",
            "status": "pending",
        })

    manifest = {
        "project_id": project_id,
        "object_class": design_spec["object_class"],
        "component_count": len(components),
        "components": components,
        "assembly_source": "output/generate.py",
        "assembly_step": "artifacts/model.step",
    }
    reports = paths.project_dir(project_id) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "component_manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
