# tests/test_v1_failures_api.py
import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.v1 import db, routes, auth
from app.core import config as cfg


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "API_DB_PATH", str(tmp_path / "r.db"))
    monkeypatch.setattr(cfg, "ADMIN_API_KEY", "admin-secret")
    c = db.connect(str(tmp_path / "r.db")); db.init_db(c)
    key, _p, _k, uid = auth.mint_key(c, "u1")
    # seed jobs: 2 failed/cad, 1 failed/internal, 1 cancelled, 1 completed (excluded)
    rows = [("f1","failed","cad","2020-01-01T00:00:01+00:00"),
            ("f2","failed","cad","2020-01-01T00:00:02+00:00"),
            ("f3","failed","internal","2020-01-01T00:00:03+00:00"),
            ("c1","cancelled",None,"2020-01-01T00:00:04+00:00"),
            ("ok","completed",None,"2020-01-01T00:00:05+00:00")]
    for jid, st, fc, ca in rows:
        c.execute("INSERT INTO jobs(job_id,user_id,project_id,status,failure_class,created_at,completed_at) "
                  "VALUES(?,?,?,?,?,?,?)", (jid, uid, "p_"+jid, st, fc, ca, ca))
    c.commit(); c.close()
    app = FastAPI(); app.include_router(routes.router)
    tc = TestClient(app); tc._akey = "admin-secret"; tc._ukey = key
    return tc


def test_failures_counts_and_recent(client):
    r = client.get("/v1/admin/jobs/failures",
                   headers={"Authorization": "Bearer admin-secret"})
    assert r.status_code == 200
    body = r.json()
    assert body["counts"].get("cad") == 2
    assert body["counts"].get("internal") == 1
    # cancelled has NULL failure_class -> grouped under a stable key
    assert sum(body["counts"].values()) == 4          # completed excluded
    assert len(body["recent"]) == 4
    assert body["recent"][0]["job_id"] == "c1"        # most recent completed_at first
    assert {"job_id", "status", "failure_class", "completed_at"} <= set(body["recent"][0])


def test_failures_respects_limit(client):
    r = client.get("/v1/admin/jobs/failures?limit=2",
                   headers={"Authorization": "Bearer admin-secret"})
    assert len(r.json()["recent"]) == 2


def test_failures_requires_admin(client):
    r = client.get("/v1/admin/jobs/failures",
                   headers={"Authorization": f"Bearer {client._ukey}"})
    assert r.status_code == 403


def test_failures_empty_db(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "API_DB_PATH", str(tmp_path / "e.db"))
    monkeypatch.setattr(cfg, "ADMIN_API_KEY", "admin-secret")
    c = db.connect(str(tmp_path / "e.db")); db.init_db(c); c.close()
    app = FastAPI(); app.include_router(routes.router)
    tc = TestClient(app)
    r = tc.get("/v1/admin/jobs/failures", headers={"Authorization": "Bearer admin-secret"})
    assert r.status_code == 200 and r.json() == {"counts": {}, "recent": []}
