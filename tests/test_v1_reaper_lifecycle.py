# tests/test_v1_reaper_lifecycle.py
import sys, importlib
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest


def _boot(tmp_path, monkeypatch, enabled):
    monkeypatch.setenv("API_DB_PATH", str(tmp_path / "r.db"))
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("API_KEY_SALT", "test-reaper-salt")
    monkeypatch.setenv("API_RATE_LIMIT_ENABLED", "0")
    monkeypatch.setenv("API_RETENTION_ENABLED", "0")
    monkeypatch.setenv("API_REAP_ORPHAN_CLAUDE", "1" if enabled else "0")
    import app.core.config as c2; importlib.reload(c2)
    import app.v1.reaper as rp2; importlib.reload(rp2)
    calls = []
    monkeypatch.setattr(rp2, "reap_orphan_claude",
                        lambda **kw: calls.append(kw) or rp2.ReapStats())
    import app.main as m; importlib.reload(m)
    from fastapi.testclient import TestClient
    with TestClient(m.app):
        pass
    return calls


def test_reaper_called_on_startup_when_enabled(tmp_path, monkeypatch):
    assert len(_boot(tmp_path, monkeypatch, enabled=True)) == 1


def test_reaper_not_called_when_disabled(tmp_path, monkeypatch):
    # main only calls reaper when the flag is on; disabled -> no call from main
    assert _boot(tmp_path, monkeypatch, enabled=False) == []
