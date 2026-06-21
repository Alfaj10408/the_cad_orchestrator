"""Pytest configuration for assembly tests."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.core import config

_TMP = Path(tempfile.mkdtemp())
config.CLAUDE_CODE_WORKSPACE_ROOT = _TMP / "runs"  # type: ignore
