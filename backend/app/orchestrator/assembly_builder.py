"""Assembly validation (deterministic). Composition lives in assembly_composer."""
from __future__ import annotations
import json
from app.core import paths


def validate_assembly(project_id: str, code: str, design_spec: dict,
                      graph: dict | None = None) -> dict:
    size = len(code.encode("utf-8"))
    solids = faces = edges = None
    bounds = {}
    insp = paths.project_dir(project_id) / "reports" / "inspection.txt"
    if insp.exists():
        try:
            s = json.loads(insp.read_text())["tokens"][0]["summary"]
            solids, faces, edges = s.get("shapeCount"), s.get("faceCount"), s.get("edgeCount")
            bounds = s.get("bounds") or {}
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

    node_count_ok = True
    bbox_within_envelope = True
    expected_nodes = None
    if graph is not None:
        expected_nodes = graph.get("node_count")
        # >= : merged/extra solids are acceptable; fewer than the graph's nodes is a failure
        node_count_ok = (solids or 0) >= (expected_nodes or 0)
        if not node_count_ok:
            flags.append("node_count_mismatch")
        env = design_spec.get("overall_envelope_mm") or {}
        mn, mx = bounds.get("min"), bounds.get("max")
        if env and mn and mx:
            dims = [mx[i] - mn[i] for i in range(3)]
            lim = [1.5 * env.get(k, 0) for k in ("x", "y", "z")]
            # envelope axis missing/0 → treat that axis as unconstrained
            bbox_within_envelope = all(dims[i] <= lim[i] or lim[i] == 0 for i in range(3))
            if not bbox_within_envelope:
                flags.append("bbox_exceeds_envelope")

    valid = (not complex_obj) or (not flags)
    report = {
        "project_id": project_id, "valid": valid, "source_bytes": size,
        "solids": solids, "faces": faces, "edges": edges,
        "primitive_box_output": bool(complex_obj and primitive_box),
        "expected_nodes": expected_nodes, "node_count_ok": node_count_ok,
        "bbox_within_envelope": bbox_within_envelope, "flags": flags,
    }
    reports = paths.project_dir(project_id) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "assembly_validation.json").write_text(json.dumps(report, indent=2))
    return report
