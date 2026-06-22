# tests/test_v1_readyz.py
import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
import shutil
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.v1 import db, routes
from app.core import config as cfg
from app.ai.llm import client as orch_client, config as orch_cfg
from app.services import claude_code_adapter

def _app(tmp_path, monkeypatch, *, queue_alive=True):
    monkeypatch.setattr(cfg, "API_DB_PATH", str(tmp_path / "r.db"))
    monkeypatch.setattr(cfg, "STORAGE_ROOT", tmp_path)
    monkeypatch.setattr(cfg, "PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr(cfg, "API_READYZ_CACHE_S", 0)        # no stale cache in tests
    db.init_db(db.connect(str(tmp_path / "r.db")))
    routes._readyz_cache.clear()
    app = FastAPI()
    class _Q:
        def alive(self): return queue_alive
        def depth(self): return 0
    app.state.queue = _Q()
    app.include_router(routes.router)
    return TestClient(app)

def test_ready_success_deps_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(orch_cfg, "ORCHESTRATOR_ENABLED", False)
    monkeypatch.setattr(cfg, "CLAUDE_CODE_ENABLED", False)
    r = _app(tmp_path, monkeypatch).get("/v1/readyz")
    assert r.status_code == 200
    j = r.json()
    assert j["ready"] is True and "timestamp" in j
    assert j["checks"]["orchestrator"] == "skipped" and j["checks"]["claude_code"] == "skipped"
    assert j["checks"]["db"] is True and j["checks"]["storage"] is True and j["checks"]["disk"] is True

def test_healthz_lightweight(tmp_path, monkeypatch):
    assert _app(tmp_path, monkeypatch).get("/v1/healthz").json() == {"ok": True}

def test_db_down_503(tmp_path, monkeypatch):
    monkeypatch.setattr(orch_cfg, "ORCHESTRATOR_ENABLED", False)
    monkeypatch.setattr(cfg, "CLAUDE_CODE_ENABLED", False)
    tc = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(routes.db, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    r = tc.get("/v1/readyz"); assert r.status_code == 503 and r.json()["checks"]["db"] is False

def test_queue_down_503(tmp_path, monkeypatch):
    monkeypatch.setattr(orch_cfg, "ORCHESTRATOR_ENABLED", False)
    monkeypatch.setattr(cfg, "CLAUDE_CODE_ENABLED", False)
    r = _app(tmp_path, monkeypatch, queue_alive=False).get("/v1/readyz")
    assert r.status_code == 503 and r.json()["checks"]["queue"] is False

def test_low_disk_503(tmp_path, monkeypatch):
    monkeypatch.setattr(orch_cfg, "ORCHESTRATOR_ENABLED", False)
    monkeypatch.setattr(cfg, "CLAUDE_CODE_ENABLED", False)
    tc = _app(tmp_path, monkeypatch)
    import collections
    Usage = collections.namedtuple("Usage", "total used free")
    monkeypatch.setattr(routes.shutil, "disk_usage", lambda p: Usage(1, 1, 1))   # ~0 free
    r = tc.get("/v1/readyz"); assert r.status_code == 503 and r.json()["checks"]["disk"] is False

def test_orchestrator_enabled_unreachable_503(tmp_path, monkeypatch):
    monkeypatch.setattr(orch_cfg, "ORCHESTRATOR_ENABLED", True)
    monkeypatch.setattr(cfg, "CLAUDE_CODE_ENABLED", False)
    tc = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(routes.orch_client, "health", lambda: {"ok": False, "detail": "x", "model": None})
    r = tc.get("/v1/readyz"); assert r.status_code == 503 and r.json()["checks"]["orchestrator"] is False

def test_claude_enabled_unauth_503(tmp_path, monkeypatch):
    monkeypatch.setattr(orch_cfg, "ORCHESTRATOR_ENABLED", False)
    monkeypatch.setattr(cfg, "CLAUDE_CODE_ENABLED", True)
    tc = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(routes.claude_code_adapter, "health",
                        lambda: {"installed": True, "authenticated": False})
    r = tc.get("/v1/readyz"); assert r.status_code == 503 and r.json()["checks"]["claude_code"] is False

def test_storage_unwritable_503(tmp_path, monkeypatch):
    monkeypatch.setattr(orch_cfg, "ORCHESTRATOR_ENABLED", False)
    monkeypatch.setattr(cfg, "CLAUDE_CODE_ENABLED", False)
    tc = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(routes, "_check_storage", lambda: False)
    r = tc.get("/v1/readyz")
    assert r.status_code == 503
    checks = r.json()["checks"]
    assert checks["storage"] is False

def test_orchestrator_raises_is_503_not_500(tmp_path, monkeypatch):
    monkeypatch.setattr(orch_cfg, "ORCHESTRATOR_ENABLED", True)
    monkeypatch.setattr(cfg, "CLAUDE_CODE_ENABLED", False)
    tc = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(routes.orch_client, "health", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    r = tc.get("/v1/readyz")
    assert r.status_code == 503
    checks = r.json()["checks"]
    assert checks["orchestrator"] is False
