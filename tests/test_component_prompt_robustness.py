# tests/test_component_prompt_robustness.py
import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
from app.orchestrator import component_validator as cv

_SPEC = {"object_class": "airplane"}
_COMP = {"name": "internal_cavity_weight_relief", "role": "weight relief",
         "source": "output/components/icwr/generate.py",
         "step": "output/components/icwr/icwr.step",
         "target_bbox_mm": {"x": 40, "y": 30, "z": 20}}


def test_prompt_marks_fillet_chamfer_cosmetic():
    p = cv.component_prompt(_SPEC, _COMP).lower()
    assert "cosmetic" in p
    # valid solid required WITHOUT fillets/chamfers
    assert "without" in p and "fillet" in p


def test_prompt_forbids_same_edge_and_orders_chamfer_first():
    p = cv.component_prompt(_SPEC, _COMP).lower()
    assert "same edge" in p
    assert "chamfer before fillet" in p


def test_prompt_requires_narrow_selection_and_straight_edges():
    p = cv.component_prompt(_SPEC, _COMP)
    assert "filter_by" in p                  # narrow selection guidance
    assert "GeomType.LINE" in p              # straight-edges-only for chamfer


def test_prompt_mandates_try_except_base_fallback():
    p = cv.component_prompt(_SPEC, _COMP)
    assert "try:" in p and "except" in p
    low = p.lower()
    assert "base" in low and ("keep" in low or "fall back" in low or "continue" in low)
    # gen_step must always return a valid solid even if cosmetics fail
    assert "always return" in low or "must always" in low
