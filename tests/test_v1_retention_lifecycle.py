# tests/test_v1_retention_lifecycle.py
import sys, asyncio
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import importlib
import pytest
from app.core import config as cfg


def test_startup_sweep_called(tmp_path, monkeypatch):
    monkeypatch.setenv("API_DB_PATH", str(tmp_path / "r.db"))
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("API_KEY_SALT", "test-retention-salt")
    monkeypatch.setenv("API_RATE_LIMIT_ENABLED", "0")
    monkeypatch.setenv("API_RETENTION_ENABLED", "1")
    monkeypatch.setenv("API_RETENTION_SWEEP_INTERVAL_S", "999999")  # no periodic fire in test
    import app.core.config as c2; importlib.reload(c2)
    import app.v1.retention as rt2; importlib.reload(rt2)
    calls = []
    monkeypatch.setattr(rt2, "sweep", lambda conn, **kw: calls.append(kw) or rt2.SweepStats(dry_run=kw.get("dry_run", True)))
    import app.main as m; importlib.reload(m)
    from fastapi.testclient import TestClient
    with TestClient(m.app):
        pass    # entering context runs lifespan startup; exiting runs shutdown
    assert any(kw.get("dry_run") is False for kw in calls)   # startup sweep ran (real)


def test_disabled_no_startup_sweep(tmp_path, monkeypatch):
    monkeypatch.setenv("API_DB_PATH", str(tmp_path / "r.db"))
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("API_KEY_SALT", "test-retention-salt")
    monkeypatch.setenv("API_RATE_LIMIT_ENABLED", "0")
    monkeypatch.setenv("API_RETENTION_ENABLED", "0")
    import app.core.config as c2; importlib.reload(c2)
    import app.v1.retention as rt2; importlib.reload(rt2)
    calls = []
    monkeypatch.setattr(rt2, "sweep", lambda conn, **kw: calls.append(kw) or rt2.SweepStats(dry_run=True))
    import app.main as m; importlib.reload(m)
    from fastapi.testclient import TestClient
    with TestClient(m.app):
        pass
    assert calls == []
