import sys, os
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
    monkeypatch.setattr(cfg, "PROJECTS_ROOT", tmp_path / "projects")
    (tmp_path / "projects").mkdir()
    monkeypatch.setattr(cfg, "ADMIN_API_KEY", "admin-secret")
    c = db.connect(str(tmp_path / "r.db")); db.init_db(c)
    uid = db.create_user(c, "u1")
    key, _pfx, _kid, _uid = auth.mint_key(c, "u1")  # returns (key, prefix, kid, user_id)
    monkeypatch.setattr("app.v1.routes._OWNER_FOR_TESTS", None, raising=False)
    c.commit(); c.close()
    app = FastAPI()
    app.include_router(routes.router)
    tc = TestClient(app)
    tc._key = key
    tc._uid = _uid
    tc._projects = tmp_path / "projects"
    tc._dbpath = str(tmp_path / "r.db")
    return tc


def _insert_job(dbpath, job_id, uid, pid, status, completed_at="2020-01-01T00:00:00+00:00"):
    c = db.connect(dbpath)
    c.execute("INSERT INTO jobs(job_id,user_id,project_id,status,created_at,completed_at) "
              "VALUES(?,?,?,?,?,?)", (job_id, uid, pid, status, "2020-01-01T00:00:00+00:00", completed_at))
    c.commit(); c.close()


def _h(tc):
    return {"Authorization": f"Bearer {tc._key}"}


def test_artifacts_410_when_purged(client):
    _insert_job(client._dbpath, "j1", client._uid, "p1", "completed")
    # no project dir on disk -> purged
    r = client.get("/v1/jobs/j1/artifacts", headers=_h(client))
    assert r.status_code == 410
    body = r.json()
    assert body["detail"] == "artifacts expired" and body["purged"] is True


def test_download_410_when_purged(client):
    _insert_job(client._dbpath, "j1", client._uid, "p1", "completed")
    r = client.get("/v1/jobs/j1/artifacts/out.step", headers=_h(client))
    assert r.status_code == 410


def test_artifacts_live_dir_not_410(client):
    _insert_job(client._dbpath, "j1", client._uid, "p1", "completed")
    (client._projects / "p1").mkdir()
    r = client.get("/v1/jobs/j1/artifacts", headers=_h(client))
    assert r.status_code == 200


def test_unknown_job_404_not_410(client):
    r = client.get("/v1/jobs/nope/artifacts", headers=_h(client))
    assert r.status_code == 404


def test_job_view_artifacts_available(client):
    _insert_job(client._dbpath, "j1", client._uid, "p1", "completed")
    r = client.get("/v1/jobs/j1", headers=_h(client))
    assert r.status_code == 200 and r.json()["artifacts_available"] is False
    (client._projects / "p1").mkdir()
    r = client.get("/v1/jobs/j1", headers=_h(client))
    assert r.json()["artifacts_available"] is True


def test_admin_sweep_dry_run_default(client):
    _insert_job(client._dbpath, "j1", client._uid, "p1", "completed")
    (client._projects / "p1").mkdir()
    r = client.post("/v1/admin/retention/sweep",
                    headers={"Authorization": "Bearer admin-secret"}, json={})
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True and body["deleted"] == 0
    assert (client._projects / "p1").exists()        # nothing deleted
    assert "duration_ms" in body


def test_admin_sweep_real_deletes(client):
    _insert_job(client._dbpath, "j1", client._uid, "p1", "completed")
    (client._projects / "p1").mkdir()
    r = client.post("/v1/admin/retention/sweep",
                    headers={"Authorization": "Bearer admin-secret"},
                    json={"dry_run": False})
    assert r.status_code == 200 and r.json()["deleted"] == 1
    assert not (client._projects / "p1").exists()


def test_admin_sweep_requires_admin(client):
    r = client.post("/v1/admin/retention/sweep", headers=_h(client), json={})
    assert r.status_code == 403
