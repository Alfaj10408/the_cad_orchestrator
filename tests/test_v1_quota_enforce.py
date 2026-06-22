import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.v1 import db, auth, routes
from app.core import config as cfg, paths

def _app(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "API_DB_PATH", str(tmp_path/"q.db"))
    monkeypatch.setattr(cfg, "API_QUOTA_ENABLED", True)
    c = db.connect(str(tmp_path/"q.db")); db.init_db(c)
    key, *_rest, uid = auth.mint_key(c, "u")
    app = FastAPI()
    class _Q:
        def enqueue(self, jid): return 1
        def depth(self): return 0
    app.state.queue = _Q()
    app.include_router(routes.router)
    return TestClient(app), c, uid, key

def test_in_flight_limit_429_and_no_project(tmp_path, monkeypatch):
    proj_root = tmp_path / "projects"
    proj_root.mkdir()
    monkeypatch.setattr(cfg, "PROJECTS_ROOT", proj_root)
    monkeypatch.setattr(paths, "PROJECTS_ROOT", proj_root)
    tc, c, uid, key = _app(tmp_path, monkeypatch)
    db.set_quota(c, uid, 50, 1)                      # max_in_flight=1
    h = {"Authorization": f"Bearer {key}"}
    r1 = tc.post("/v1/jobs", json={"prompt": "a block"}, headers=h)
    assert r1.status_code == 201
    r2 = tc.post("/v1/jobs", json={"prompt": "a block"}, headers=h)  # 1 already in-flight
    assert r2.status_code == 429 and r2.json()["detail"]["scope"] == "in_flight"
    # no orphan project created for the rejected request
    import os
    projects = os.listdir(proj_root)
    assert len(projects) == 1   # only the first (accepted) job's project

def test_daily_limit_429(tmp_path, monkeypatch):
    tc, c, uid, key = _app(tmp_path, monkeypatch)
    db.set_quota(c, uid, 1, 50)                      # daily=1, in_flight high
    h = {"Authorization": f"Bearer {key}"}
    assert tc.post("/v1/jobs", json={"prompt":"x"}, headers=h).status_code == 201
    r = tc.post("/v1/jobs", json={"prompt":"x"}, headers=h)
    assert r.status_code == 429 and r.json()["detail"]["scope"] == "daily"

def test_admin_bypass(tmp_path, monkeypatch):
    tc, c, uid, key = _app(tmp_path, monkeypatch)
    # promote user to admin in DB, set tiny limits -> still allowed
    c.execute("UPDATE users SET is_admin=1 WHERE id=?", (uid,)); c.commit()
    db.set_quota(c, uid, 1, 1)
    h = {"Authorization": f"Bearer {key}"}
    assert tc.post("/v1/jobs", json={"prompt":"x"}, headers=h).status_code == 201
    assert tc.post("/v1/jobs", json={"prompt":"x"}, headers=h).status_code == 201  # bypass

def test_quota_disabled(tmp_path, monkeypatch):
    tc, c, uid, key = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(cfg, "API_QUOTA_ENABLED", False)
    db.set_quota(c, uid, 1, 1)
    h = {"Authorization": f"Bearer {key}"}
    assert tc.post("/v1/jobs", json={"prompt":"x"}, headers=h).status_code == 201
    assert tc.post("/v1/jobs", json={"prompt":"x"}, headers=h).status_code == 201
