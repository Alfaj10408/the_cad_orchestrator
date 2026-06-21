import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import json
from app.orchestrator import assembly_builder
from app.core import paths


def _write_insp(pid, solids, faces, edges, bounds):
    d = paths.project_dir(pid) / "reports"; d.mkdir(parents=True, exist_ok=True)
    payload = {"tokens": [{"summary": {"shapeCount": solids, "faceCount": faces,
               "edgeCount": edges, "bounds": bounds}}]}
    (d / "inspection.txt").write_text(json.dumps(payload))


SPEC = {"complex": True, "explicit_primitive": False,
        "overall_envelope_mm": {"x": 250.0, "y": 250.0, "z": 120.0}}


def test_structural_node_count_mismatch_fails():
    pid = "valasm_t1"
    _write_insp(pid, solids=3, faces=100, edges=300,
                bounds={"min": [-100, -90, 0], "max": [100, 90, 100]})
    graph = {"node_count": 5}
    r = assembly_builder.validate_assembly(pid, "x" * 2000, SPEC, graph=graph)
    assert r["node_count_ok"] is False and r["valid"] is False


def test_valid_assembly_passes():
    pid = "valasm_t2"
    _write_insp(pid, solids=5, faces=100, edges=300,
                bounds={"min": [-100, -90, 0], "max": [100, 90, 100]})
    graph = {"node_count": 5}
    r = assembly_builder.validate_assembly(pid, "x" * 2000, SPEC, graph=graph)
    assert r["valid"] is True and r["node_count_ok"] and r["bbox_within_envelope"]


def test_bbox_exceeds_envelope_fails():
    """Assembly whose x-span (800 mm) exceeds 1.5× envelope x (375 mm) must fail."""
    pid = "valasm_t3"
    # solid count matches graph node count so ONLY the bbox check should fail
    _write_insp(pid, solids=5, faces=100, edges=300,
                bounds={"min": [-400, -90, 0], "max": [400, 90, 100]})
    graph = {"node_count": 5}
    r = assembly_builder.validate_assembly(pid, "x" * 2000, SPEC, graph=graph)
    assert r["bbox_within_envelope"] is False
    assert "bbox_exceeds_envelope" in r["flags"]
    assert r["valid"] is False


def test_no_inspection_fails_closed():
    """When graph is present but inspection.txt is absent, validate_assembly must fail closed."""
    pid = "valasm_t4_no_insp"
    # Ensure no inspection.txt exists for this fresh project id
    insp = paths.project_dir(pid) / "reports" / "inspection.txt"
    if insp.exists():
        insp.unlink()
    graph = {"node_count": 3}
    r = assembly_builder.validate_assembly(pid, "x" * 2000, SPEC, graph=graph)
    assert r["valid"] is False
    assert "no_inspection" in r["flags"]
