# tests/test_repair_hint_edges.py
import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
from app.orchestrator import component_validator as cv


def test_chamfer_on_consumed_edge_gets_edge_hint():
    reason = "BRep_API: chamfer failed, no faces for edge (edge not found)"
    h = cv._repair_hint(reason).lower()
    assert "no longer exist" in h or "curved" in h
    assert "chamfer before the fillet" in h
    assert "geomtype.line" in h


def test_fillet_edge_failure_gets_edge_hint():
    reason = "Standard_NullObject: fillet on edge produced null shape"
    h = cv._repair_hint(reason).lower()
    assert "try/except" in h and "base solid" in h


def test_plain_chamfer_length_still_gets_length_hint():
    # a chamfer-length complaint WITHOUT edge keywords keeps the existing hint
    reason = "chamfer length too large for the face"
    h = cv._repair_hint(reason).lower()
    assert "length" in h and "reduce" in h


def test_fillet_radius_branch_unchanged():
    reason = "fillet radius exceeds max_fillet for this geometry"
    h = cv._repair_hint(reason).lower()
    assert "radius" in h


def test_unrelated_reason_no_hint():
    assert cv._repair_hint("disk full") == ""
