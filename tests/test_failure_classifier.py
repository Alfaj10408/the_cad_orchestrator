import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services.claude_code_adapter import classify_failure


def test_quota():
    assert classify_failure(is_error=True,
        result_text="You've hit your session limit · resets 12:30pm (UTC)",
        subtype="success", num_turns=1, max_turns=15) == "quota"


def test_turns():
    assert classify_failure(is_error=True, result_text="",
        subtype="error_max_turns", num_turns=16, max_turns=15) == "turns"


def test_cad_other():
    assert classify_failure(is_error=True, result_text="boom",
        subtype="error", num_turns=3, max_turns=15) == "cad"


def test_ok():
    assert classify_failure(is_error=False, result_text="done",
        subtype="success", num_turns=2, max_turns=15) is None
