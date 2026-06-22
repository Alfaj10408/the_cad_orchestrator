# tests/test_v1_claims_api.py
import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
from datetime import datetime, timezone, timedelta
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.v1 import db, routes, auth
from app.core import config as cfg


def _iso(dt): return dt.isoformat()
def _now(): return datetime.now(timezone.utc)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "API_DB_PATH", str(tmp_path / "r.db"))
    monkeypatch.setattr(cfg, "ADMIN_API_KEY", "admin-secret")
    c = db.connect(str(tmp_path / "r.db")); db.init_db(c)
    key, _p, _k, _u = auth.mint_key(c, "u1")
    # two running claims (one fresh lease, one expired=stale) + one pending (excluded)
    fut = _iso(_now() + timedelta(seconds=100))
    past = _iso(_now() - timedelta(seconds=100))
    c.execute("INSERT INTO jobs(job_id,user_id,project_id,status,created_at,claimed_by,claimed_at,lease_expires_at) "
              "VALUES('jr1','u1','p1','running',?,?,?,?)", ("2020-01-01T00:00:00+00:00","wA","2020-01-01T00:00:00+00:00",fut))
    c.execute("INSERT INTO jobs(job_id,user_id,project_id,status,created_at,claimed_by,claimed_at,lease_expires_at) "
              "VALUES('jr2','u1','p2','running',?,?,?,?)", ("2020-01-01T00:00:01+00:00","wA","2020-01-01T00:00:01+00:00",past))
    c.execute("INSERT INTO jobs(job_id,user_id,project_id,status,created_at) "
              "VALUES('jp','u1','p3','pending',?)", ("2020-01-01T00:00:02+00:00",))
    c.commit(); c.close()
    app = FastAPI(); app.include_router(routes.router)
    tc = TestClient(app); tc._ukey = key
    return tc


def _admin(): return {"Authorization": "Bearer admin-secret"}


def test_claims_lists_running_with_stale_flag(client):
    r = client.get("/v1/admin/claims", headers=_admin())
    assert r.status_code == 200
    body = r.json()
    ids = {c["job_id"]: c for c in body["claims"]}
    assert set(ids) == {"jr1", "jr2"}          # pending excluded
    assert ids["jr1"]["stale"] is False         # future lease
    assert ids["jr2"]["stale"] is True          # past lease
    assert ids["jr1"]["claimed_by"] == "wA"
    assert "now" in body


def test_claims_by_owner_grouping(client):
    body = client.get("/v1/admin/claims", headers=_admin()).json()
    owners = {o["claimed_by"]: o for o in body["by_owner"]}
    assert owners["wA"]["running"] == 2


def test_claims_requires_admin(client):
    r = client.get("/v1/admin/claims", headers={"Authorization": f"Bearer {client._ukey}"})
    assert r.status_code == 403


def test_claims_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "API_DB_PATH", str(tmp_path / "e.db"))
    monkeypatch.setattr(cfg, "ADMIN_API_KEY", "admin-secret")
    c = db.connect(str(tmp_path / "e.db")); db.init_db(c); c.close()
    app = FastAPI(); app.include_router(routes.router)
    r = TestClient(app).get("/v1/admin/claims", headers={"Authorization": "Bearer admin-secret"})
    assert r.status_code == 200
    body = r.json()
    assert body["claims"] == [] and body["by_owner"] == []
