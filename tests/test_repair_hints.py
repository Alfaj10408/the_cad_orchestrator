import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
from app.orchestrator import component_validator as cv

_COMP = {"name": "arm"}

def test_fillet_hint():
    r = cv.repair_prompt(_COMP, "FilletError: creating a fillet with radius of 0.2, try a smaller value or use max_fillet()")
    low = r.lower()
    assert "max_fillet" in low and "reduce" in low
    assert "fillet with radius of 0.2" in r          # original error preserved verbatim
    assert "edit tool" in low                          # generic Edit instruction preserved

def test_chamfer_hint():
    assert "chamfer" in cv.repair_prompt(_COMP, "ChamferError: chamfer length too large").lower()

def test_api_hint():
    low = cv.repair_prompt(_COMP, "NameError: name 'translate' is not defined").lower()
    assert "location" in low and "translate()" in low

def test_empty_solid_hint():
    low = cv.repair_prompt(_COMP, "degenerate/empty (shapes=0, bounds={})").lower()
    assert "positive-volume" in low

def test_unknown_error_no_hint():
    # unknown error -> no HINT line, but generic Edit instruction + error remain
    r = cv.repair_prompt(_COMP, "some unexpected failure xyz")
    assert "HINT:" not in r
    assert "some unexpected failure xyz" in r
    assert "Edit tool" in r

def test_repair_hint_unknown_returns_empty():
    assert cv._repair_hint("totally unrelated message") == ""

def test_typeerror_unexpected_keyword_hint():
    low = cv.repair_prompt(_COMP, "TypeError: BuildSketch.__init__() got an unexpected keyword argument 'origin'").lower()
    assert "signature" in low and "buildsketch" in low
    assert "unexpected keyword argument 'origin'" in cv.repair_prompt(_COMP, "TypeError: ... unexpected keyword argument 'origin'") or "origin=" in low

def test_positional_argument_hint():
    low = cv.repair_prompt(_COMP, "TypeError: foo() takes 2 positional arguments but 3 were given").lower()
    assert "api/signature" in low or "signature" in low

def test_nameerror_still_matches_api():
    low = cv.repair_prompt(_COMP, "NameError: name 'translate' is not defined").lower()
    assert "translate()" in low and ("location" in low or "signature" in low)
