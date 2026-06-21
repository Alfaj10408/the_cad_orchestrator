import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services import claude_code_adapter as adapter


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
