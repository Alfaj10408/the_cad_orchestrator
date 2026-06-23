import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
_PROMPT = _BACKEND / "app" / "ai" / "prompts" / "worker_prompt_system.txt"


def test_planning_prompt_has_cosmetic_fillet_note():
    text = _PROMPT.read_text().lower()
    assert "cosmetic" in text
    assert "same edge" in text
    assert "small" in text and "local" in text
