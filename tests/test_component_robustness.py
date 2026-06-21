import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

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
