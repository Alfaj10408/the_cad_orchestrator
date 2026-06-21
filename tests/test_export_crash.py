import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
from app.orchestrator import component_validator as cv


def test_is_crash_exit():
    assert cv._is_crash_exit(-11) is True     # SIGSEGV (Python signal form)
    assert cv._is_crash_exit(139) is True      # 128+11 (shell form)
    assert cv._is_crash_exit(134) is True      # SIGABRT
    assert cv._is_crash_exit(1) is False       # ordinary tool error
    assert cv._is_crash_exit(0) is False
    assert cv._is_crash_exit(None) is False
