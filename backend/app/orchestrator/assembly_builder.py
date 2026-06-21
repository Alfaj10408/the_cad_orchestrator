"""Assembly stage: compose validated components, then validate the assembly.

Runs only after every component passes. The assembly source becomes the project
generator (output/generate.py -> source/model.py) and is executed by the existing
CAD pipeline (STEP/STL/GLB/inspect/snapshot), so downstream stays unchanged.
"""
from __future__ import annotations

import json

from app.core import paths


def assembly_prompt(design_spec: dict, manifest: dict, validation: dict) -> str:
    """Claude prompt to assemble the validated components into one model."""
    comp_lines = []
    for c in manifest["components"]:
        v = next((r for r in validation["components"] if r["name"] == c["name"]), None)
        status = "VALID" if (v and v["valid"]) else "check"
        comp_lines.append(
            f"  - {c['name']} x{c['quantity']} ({c['role']}) [{status}] "
            f"source: {c['source']}"
        )
    comps = "\n".join(comp_lines)
    env = design_spec["overall_envelope_mm"]

    return f"""Use the installed cad skill.

All components of this {design_spec['object_class']} have been generated and
independently validated. Now build the ASSEMBLY.

Write the assembly generator at output/generate.py defining exactly
`def gen_step():` that returns ONE labeled build123d assembly Compound of the
full {design_spec['object_kind']}.

Each validated component already exists in the workspace (read them for the exact
geometry you produced):
{comps}

Assembly requirements:
- Reuse the SAME geometry you authored per component (re-create each component's
  solid as a helper function in this file, matching its validated source).
- Place components with explicit Location transforms and the right quantities,
  e.g. four arms at 0/90/180/270 degrees, a motor pod + propeller at each arm
  end, landing gear and camera mount under the body, battery bay and controller
  deck on the fuselage. Maintain fourfold symmetry where applicable.
- Label parts; combine into a single assembly Compound returned by gen_step().
- Overall envelope about {env['x']} x {env['y']} x {env['z']} mm.
- Closed, positive-volume solids. No file/network I/O; no os/subprocess/socket/
  shutil/pathlib/requests, no open()/eval()/exec()/__import__. Do NOT execute.

The backend will STEP-export from this generator, run inspection
(refs --facts --planes --positioning), a MANDATORY snapshot, secondary
STL/3MF/GLB, then CAD Viewer handoff. The result MUST be a recognizable
{design_spec['object_kind']} with multiple components — NOT a single box.
"""


def repair_prompt(reason: str) -> str:
    return (
        f"\n\n--- REWRITE REQUIRED (assembly) ---\n{reason}\n"
        "The assembly must contain multiple distinct components and look like the "
        "requested object, not a primitive box. Fix the smallest responsible "
        "section and regenerate.\n"
    )


def validate_assembly(project_id: str, code: str, design_spec: dict) -> dict:
    """Anti-primitive assembly gate from the project inspection report."""
    size = len(code.encode("utf-8"))
    solids = faces = edges = None
    insp = paths.project_dir(project_id) / "reports" / "inspection.txt"
    if insp.exists():
        try:
            s = json.loads(insp.read_text())["tokens"][0]["summary"]
            solids, faces, edges = s.get("shapeCount"), s.get("faceCount"), s.get("edgeCount")
        except Exception:  # noqa: BLE001
            pass

    primitive_box = solids == 1 and faces == 6 and edges == 12
    complex_obj = design_spec.get("complex", True) and not design_spec.get("explicit_primitive", False)

    flags = []
    if complex_obj and size < 1200:
        flags.append("low_detail_output")
    if complex_obj and primitive_box:
        flags.append("primitive_box_output")
    if complex_obj and not (((solids or 0) > 1) or ((faces or 0) > 6)):
        flags.append("insufficient_complexity")

    valid = (not complex_obj) or (not flags)
    report = {
        "project_id": project_id,
        "valid": valid,
        "source_bytes": size,
        "solids": solids, "faces": faces, "edges": edges,
        "primitive_box_output": bool(complex_obj and primitive_box),
        "flags": flags,
    }
    reports = paths.project_dir(project_id) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "assembly_validation.json").write_text(json.dumps(report, indent=2))
    return report
