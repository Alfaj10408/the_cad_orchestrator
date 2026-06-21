import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import json
from app.core import paths
from app.services import claude_code_adapter as adapter
from app.core import config as cfg
from app.orchestrator import component_validator as cv


def test_build_claude_argv_uses_passed_tools_and_turns():
    argv = adapter.build_claude_argv(prompt="hi", model="sonnet", max_turns=8,
                                     tools="Read,Write,Edit")
    assert "--tools" in argv
    assert argv[argv.index("--tools") + 1] == "Read,Write,Edit"
    assert "--max-turns" in argv
    assert argv[argv.index("--max-turns") + 1] == "8"
    assert "--model" in argv and argv[argv.index("--model") + 1] == "sonnet"
    assert argv[-1] == "hi"  # prompt is the final argv value
    assert "Bash" not in argv[argv.index("--tools") + 1]


def test_component_config_defaults():
    assert cfg.CLAUDE_CODE_COMPONENT_TOOLS == "Read,Write,Edit"
    assert "Bash" not in cfg.CLAUDE_CODE_COMPONENT_TOOLS
    assert cfg.CLAUDE_CODE_COMPONENT_MAX_TURNS == 8
    assert cfg.CLAUDE_CODE_COMPONENT_NEAR_CAP == 6
    # globals unchanged
    assert cfg.CLAUDE_CODE_TOOLS == "Read,Write,Edit,Bash"
    assert cfg.CLAUDE_CODE_MAX_TURNS == 15


_SPEC = {"object_class": "quadcopter drone assembly"}
_COMP = {"name": "fuselage", "role": "central body",
         "source": "output/components/fuselage/generate.py",
         "target_bbox_mm": {"x": 112.5, "y": 112.5, "z": 60.0}}


def test_component_prompt_has_preflight_and_path():
    p = cv.component_prompt(_SPEC, _COMP)
    assert "output/components/fuselage/generate.py" in p
    low = p.lower()
    assert "exactly one file" in low
    assert "do not execute" in low
    assert "no shell" in low
    assert "stop after" in low
    assert "def gen_step()" in p  # existing requirement preserved


def test_repair_prompt_is_edit_targeted_and_carries_error():
    r = cv.repair_prompt(_COMP, "NameError: name 'translate' is not defined")
    low = r.lower()
    assert "edit" in low
    assert "smallest" in low or "only the failing" in low
    assert "do not rewrite" in low or "do not execute" in low
    assert "NameError: name 'translate' is not defined" in r  # exact error included


def test_write_metrics_schema_and_totals():
    pid = "metrics_t"
    records = [
        {"name": "a", "attempts": 1, "repairs": 0, "turns_total": 4,
         "turns_per_attempt": [4], "duration_seconds": 12.5, "failure_class": None,
         "source_bytes": 1800, "valid": True, "reason": None},
        {"name": "b", "attempts": 2, "repairs": 1, "turns_total": 9,
         "turns_per_attempt": [5, 4], "duration_seconds": 30.0, "failure_class": None,
         "source_bytes": 1500, "valid": True, "reason": None},
    ]
    rep = cv.write_metrics(pid, records)
    assert rep["totals"]["components"] == 2
    assert rep["totals"]["passed"] == 2
    assert rep["totals"]["turns_total"] == 13
    assert rep["totals"]["repairs_total"] == 1
    assert round(rep["totals"]["avg_turns_per_component"], 1) == 6.5
    assert round(rep["totals"]["duration_total_s"], 1) == 42.5
    on_disk = json.loads((paths.project_dir(pid) / "reports" / "component_metrics.json").read_text())
    assert on_disk["components"][0]["name"] == "a"
    assert on_disk["components"][0]["duration_seconds"] == 12.5
