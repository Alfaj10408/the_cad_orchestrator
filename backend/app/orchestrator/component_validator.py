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
    return f"""PREFLIGHT — follow exactly:
- Your FIRST tool action MUST be `Write` to {comp['source']}. Do NOT Read or list anything before writing.
- Do NOT inspect directories, do not list the workspace, do not probe whether {comp['source']} exists,
  and do not read or inspect /root/.claude or any plugins. The target directory is created for you.
- Assume the target path is correct. Write the file immediately from your own build123d knowledge —
  do NOT read skill files or plugin files.
- Output EXACTLY ONE file at {comp['source']}. Create no other files. No shell. No Bash.
- After writing, STOP. The backend validates and, if needed, sends a targeted repair.

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
- Closed, positive-volume solid; manufacturable.
- Fillets and chamfers are COSMETIC: the component MUST be a valid closed
  positive-volume solid WITHOUT any fillet/chamfer. Add them only as a finishing
  touch, following these rules:
  * Never apply fillet() and chamfer() to the SAME edge or overlapping edge sets.
  * If both are needed on nearby edges, do CHAMFER BEFORE FILLET (chamfer needs
    the original straight edge; fillet the remaining straight edges after).
  * Select a NARROW, specific edge set for each cosmetic op — never the global
    part.edges(); use e.g. .edges().filter_by(Axis.Z) or one face's edges.
  * Chamfer only STRAIGHT edges: .edges().filter_by(GeomType.LINE). Never chamfer
    curved or already-filleted edges.
  * Keep radius/length small relative to the local wall; prefer max_fillet().
  * Build the valid solid FIRST, then wrap EACH cosmetic fillet/chamfer in its own
    try/except; on ANY exception keep the pre-cosmetic solid and continue, e.g.:
        base = part
        try:
            part = fillet(part.edges().filter_by(Axis.Z), radius=r)
        except Exception:
            part = base   # cosmetic failed -> keep the valid base solid
    gen_step() MUST always return a valid positive-volume solid even if every
    cosmetic op fails.
- No file/network I/O; no os/subprocess/socket/shutil/pathlib/requests,
  no open()/eval()/exec()/__import__.

The backend will STEP-export and inspect this component to validate it. Make it
a clean, recognizable {comp['name']}.
"""


def _repair_hint(reason: str) -> str:
    r = (reason or "").lower()
    if "fillet" in r and ("radius" in r or "max_fillet" in r):
        return ("\nHINT: the fillet radius is too large for this geometry. Substantially "
                "reduce it, use max_fillet() to compute a safe radius, or remove the fillet "
                "on those edges.\n")
    if "chamfer" in r:
        return "\nHINT: the chamfer length is too large. Reduce it or remove the chamfer.\n"
    if ("not defined" in r or "nameerror" in r or "attributeerror" in r
            or "typeerror" in r or "unexpected keyword" in r
            or "positional argument" in r):
        return ("\nHINT: build123d API/signature error. Use only valid build123d calls with "
                "correct signatures: BuildSketch takes plane(s) positionally "
                "(e.g. `BuildSketch(Plane.XY)`); place sketch geometry with `Locations(...)`/"
                "`Pos(...)`; position solids with `.moved(Location(...))`. Do NOT pass keyword "
                "args the constructor rejects (e.g. `origin=`, `center=`), and do not call "
                "undefined names (e.g. `translate()`). Fix the call to match the real API.\n")
    if "degenerate" in r or "no solid" in r or "empty" in r:
        return ("\nHINT: the result has no positive-volume solid. Ensure boolean ops do not "
                "remove all material and that all dimensions are > 0.\n")
    return ""


def repair_prompt(comp: dict, reason: str) -> str:
    return (
        f"\n\n--- REWRITE REQUIRED (component {comp['name']}) ---\n{reason}\n"
        "Use the Edit tool to change ONLY the failing line/section identified by "
        "the error above. Do not rewrite the whole file. Do not execute or test. "
        "Make the smallest fix that yields a single closed positive-volume solid; "
        "the backend re-validates.\n"
        + _repair_hint(reason)
    )


def _is_crash_exit(returncode) -> bool:
    """True for signal/crash-class exits (Python signals are negative; shells
    report 128+signal). Ordinary nonzero tool errors are NOT crash-class."""
    return returncode is not None and (returncode < 0 or returncode >= 128)


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
    step_exists = (ws / step_rel).exists()
    crash = _is_crash_exit(step.returncode)
    # Geometry is the source of truth: a crash-class exit (e.g. a post-export
    # SIGSEGV in GLB/cleanup) is tolerated IF a STEP was produced and inspects
    # to a valid solid. Ordinary nonzero exits and missing STEPs still fail.
    if not step_exists or (step.returncode != 0 and not crash):
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
    if crash and facts is None:
        return {"ok": False,
                "reason": f"STEP export crashed (rc={step.returncode}) and produced no inspectable solid",
                "facts": None}
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


def write_metrics(project_id: str, records: list[dict]) -> dict:
    passed = sum(1 for r in records if r.get("valid"))
    turns_total = sum(r.get("turns_total") or 0 for r in records)
    repairs_total = sum(r.get("repairs") or 0 for r in records)
    duration_total = sum(r.get("duration_seconds") or 0 for r in records)
    n = len(records)
    report = {
        "project_id": project_id,
        "components": records,
        "totals": {
            "components": n,
            "passed": passed,
            "turns_total": turns_total,
            "repairs_total": repairs_total,
            "avg_turns_per_component": (turns_total / n) if n else 0,
            "duration_total_s": round(duration_total, 1),
        },
    }
    reports = paths.project_dir(project_id) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "component_metrics.json").write_text(json.dumps(report, indent=2))
    return report
