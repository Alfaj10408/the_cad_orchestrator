"""Independent per-component generation prompts, execution, and validation.

Each component is built and checked on its own (small Claude task + standalone
STEP export + inspection) before any assembly is attempted.
"""
from __future__ import annotations

import json

from app.core import paths
from app.services import cad_runner, claude_code_adapter, llm_cad_generator


def component_prompt(design_spec: dict, comp: dict) -> str:
    """Skill-aware Claude prompt for ONE component (small, focused task)."""
    bb = comp["target_bbox_mm"]
    return f"""Use the installed cad skill.

You are building ONE component of a {design_spec['object_class']}: the
`{comp['name']}` ({comp['role']}).

Write a STEP-first build123d source file at {comp['source']} defining exactly
`def gen_step():` that returns a SINGLE closed, positive-volume build123d Solid
(or a small Compound) for ONLY this component — not the whole assembly.

Requirements:
- Start with: from build123d import *
- Named parameters in millimeters near the top.
- Origin at the component's own center, XY base plane, +Z up.
- Approximate target envelope: {bb['x']} x {bb['y']} x {bb['z']} mm (a guide, not exact).
- Closed, positive-volume solid; manufacturable; fillets/chamfers where natural.
- No file/network I/O; no os/subprocess/socket/shutil/pathlib/requests,
  no open()/eval()/exec()/__import__. Do NOT execute the code.

The backend will STEP-export and inspect this component to validate it before
assembly. Make it a clean, recognizable {comp['name']}.
"""


def repair_prompt(comp: dict, reason: str) -> str:
    return (
        f"\n\n--- REWRITE REQUIRED (component {comp['name']}) ---\n{reason}\n"
        "Fix the smallest responsible part of the source so it produces a single "
        "closed positive-volume solid, then it will be re-validated.\n"
    )


def run_component(project_id: str, comp: dict, code: str) -> dict:
    """Write + STEP-export + inspect one component. Returns exec facts."""
    ok, safety = llm_cad_generator.check_code_safety(code)
    if not ok:
        return {"ok": False, "reason": f"unsafe: {safety}", "facts": None}

    ws = claude_code_adapter.workspace_dir(project_id)
    src_rel = comp["source"]
    step_rel = comp["step"]
    (ws / src_rel).parent.mkdir(parents=True, exist_ok=True)
    (ws / src_rel).write_text(code)

    step = cad_runner._run(cad_runner.STEP_TOOL, [f"{src_rel}={step_rel}"], cwd=ws)
    if step.returncode != 0 or not (ws / step_rel).exists():
        return {"ok": False, "reason": (step.stderr or "STEP export failed")[-800:], "facts": None}

    ref = step_rel.rsplit(".", 1)[0]
    insp = cad_runner._run(
        cad_runner.INSPECT_TOOL, ["refs", "--facts", f"@cad[{ref}]"], cwd=ws
    )
    facts = None
    try:
        facts = json.loads(insp.stdout)["tokens"][0]["summary"]
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "reason": None, "facts": facts}


def validate_component(comp: dict, exec_result: dict) -> dict:
    """Per-component gate: exported, has a solid, non-degenerate bounds."""
    base = {"name": comp["name"], "step": comp["step"]}
    if not exec_result["ok"]:
        return {**base, "valid": False, "reason": exec_result["reason"], "facts": None}

    facts = exec_result["facts"]
    if not facts:
        return {**base, "valid": False, "reason": "no inspection facts", "facts": None}

    shapes = facts.get("shapeCount") or 0
    b = facts.get("bounds") or {}
    mn, mx = b.get("min"), b.get("max")
    dims_ok = bool(mn and mx and all((mx[i] - mn[i]) > 0.1 for i in range(3)))
    valid = shapes >= 1 and dims_ok
    reason = None if valid else f"degenerate/empty (shapes={shapes}, bounds={b})"
    return {
        **base, "valid": valid, "reason": reason,
        "facts": {"shapeCount": shapes, "faceCount": facts.get("faceCount"),
                  "edgeCount": facts.get("edgeCount"), "bounds": b},
    }


def write_report(project_id: str, results: list[dict]) -> dict:
    report = {
        "project_id": project_id,
        "total": len(results),
        "passed": sum(1 for r in results if r["valid"]),
        "components": results,
    }
    reports = paths.project_dir(project_id) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "component_validation.json").write_text(json.dumps(report, indent=2))
    return report
