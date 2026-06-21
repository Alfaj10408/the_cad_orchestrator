"""MVP v1 human-readable report generator (deterministic, no LLM).

Assembles reports/summary.md from brief.json + reports/inspection.txt.
"""
from __future__ import annotations

import json

from app.core import paths

_ARTIFACTS = [
    "source/model.py",
    "cad/model.step",
    "cad/model.stl",
    "cad/model.glb",
    "cad/snapshot.png",
    "reports/inspection.txt",
]

_LIMITATIONS = [
    "Deterministic CAD template only.",
    "Not free-form AI CAD yet.",
    "Simple bracket / block / enclosure support only.",
    "No URDF/SRDF/mechatronic/G-code/Bambu in MVP v1.",
]


def _load_json(path):
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return None


def _inspection_summary(root) -> list[str]:
    """Return markdown lines summarizing reports/inspection.txt."""
    insp_path = root / "reports" / "inspection.txt"
    if not insp_path.exists():
        return ["- Inspection file not found."]

    data = _load_json(insp_path)
    summary = None
    if isinstance(data, dict):
        tokens = data.get("tokens") or []
        if tokens and isinstance(tokens[0], dict):
            summary = tokens[0].get("summary")

    if not isinstance(summary, dict):
        return ["- Inspection file was generated but not summarized."]

    lines: list[str] = []
    if "kind" in summary:
        lines.append(f"- Kind: {summary['kind']}")
    if "shapeCount" in summary:
        lines.append(f"- Solids/shapes: {summary['shapeCount']}")
    if "faceCount" in summary:
        lines.append(f"- Faces: {summary['faceCount']}")
    if "edgeCount" in summary:
        lines.append(f"- Edges: {summary['edgeCount']}")
    bounds = summary.get("bounds")
    if isinstance(bounds, dict) and "min" in bounds and "max" in bounds:
        lines.append(f"- Bounds min: {bounds['min']}")
        lines.append(f"- Bounds max: {bounds['max']}")
    return lines or ["- Inspection file was generated but not summarized."]


def generate_summary_report(project_id: str) -> str:
    """Write reports/summary.md. Returns the relative report path."""
    root = paths.project_dir(project_id)
    brief = _load_json(root / "brief.json") or {}
    params = brief.get("parameters", {}) or {}
    answers = brief.get("user_answers", {}) or {}

    prompt = brief.get("prompt", "(unknown)")
    intent = brief.get("intent", "(unknown)")
    dimensions = params.get("dimensions") or answers.get("dimensions") or "(not specified)"
    material = params.get("material") or answers.get("material") or "(not specified)"
    assumptions = brief.get("assumptions", []) or []

    lines: list[str] = []
    lines.append(f"# CAD Generation Report — {project_id}")
    lines.append("")
    lines.append(f"**Project ID:** {project_id}")
    lines.append("")
    lines.append("## Prompt")
    lines.append(f"> {prompt}")
    lines.append("")
    lines.append("## Intent")
    lines.append(f"- {intent}")
    lines.append("")
    lines.append("## Specification")
    lines.append(f"- Dimensions: {dimensions}")
    lines.append(f"- Material: {material}")
    lines.append("")
    lines.append("## Assumptions")
    if assumptions:
        for a in assumptions:
            lines.append(f"- {a}")
    else:
        lines.append("- None recorded.")
    lines.append("")
    lines.append("## Generated Artifacts")
    for art in _ARTIFACTS:
        exists = (root / art).exists()
        mark = "✅" if exists else "❌"
        lines.append(f"- {mark} `{art}`")
    lines.append("")
    lines.append("## CAD Inspection Summary")
    lines.extend(_inspection_summary(root))
    lines.append("")
    lines.append("## Limitations")
    for lim in _LIMITATIONS:
        lines.append(f"- {lim}")
    lines.append("")

    report_rel = "reports/summary.md"
    (root / "reports").mkdir(parents=True, exist_ok=True)
    (root / report_rel).write_text("\n".join(lines))
    return report_rel
