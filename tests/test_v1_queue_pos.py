import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
from app.v1 import db

def _conn(tmp_path):
    c = db.connect(str(tmp_path / "qp.db")); db.init_db(c); return c

def _add(c, jid, status="pending"):
    db.insert_job(c, jid, "u1", "p_"+jid, status="pending")
    if status != "pending":
        db.update_job(c, jid, status=status)

def test_pending_position_ranks_by_created_at(tmp_path):
    c = _conn(tmp_path)
    for jid in ("a", "b", "c"):       # inserted in order → created_at ascending
        _add(c, jid)
    assert db.pending_position(c, "a") == 1
    assert db.pending_position(c, "b") == 2
    assert db.pending_position(c, "c") == 3

def test_non_pending_returns_none(tmp_path):
    c = _conn(tmp_path)
    _add(c, "a"); _add(c, "b")
    db.update_job(c, "a", status="running")     # a leaves pending set
    assert db.pending_position(c, "a") is None   # not pending → None
    assert db.pending_position(c, "b") == 1      # b shifts to 1
    assert db.pending_position(c, "missing") is None

# --- route-level tests ---
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.v1 import auth, routes
from app.core import config as cfg
import app.v1.db as _db_mod

def _app_route(tmp_path, monkeypatch):
    db_path = str(tmp_path / "r.db")
    monkeypatch.setattr(cfg, "API_DB_PATH", db_path)
    monkeypatch.setattr(_db_mod.config, "API_DB_PATH", db_path)
    c = db.connect(db_path); db.init_db(c)
    key, *_ = auth.mint_key(c, "u")
    app = FastAPI()
    class _Q:
        def enqueue(self, jid): return 1
        def depth(self): return 0
    app.state.queue = _Q()
    app.include_router(routes.router)
    return app, key, c

def test_route_queue_pos_is_live(tmp_path, monkeypatch):
    app, key, c = _app_route(tmp_path, monkeypatch)
    tc = TestClient(app); h = {"Authorization": f"Bearer {key}"}
    ids = [tc.post("/v1/jobs", json={"prompt": f"a {i}x10x5 block"}, headers=h).json()["job_id"]
           for i in range(3)]
    pos = lambda jid: tc.get(f"/v1/jobs/{jid}", headers=h).json()["queue_pos"]
    assert [pos(j) for j in ids] == [1, 2, 3]
    db.update_job(c, ids[0], status="running")
    assert pos(ids[0]) is None and pos(ids[1]) == 1 and pos(ids[2]) == 2
    db.update_job(c, ids[1], status="cancelled")
    assert pos(ids[1]) is None and pos(ids[2]) == 1
    db.update_job(c, ids[2], status="completed")
    assert pos(ids[2]) is None
