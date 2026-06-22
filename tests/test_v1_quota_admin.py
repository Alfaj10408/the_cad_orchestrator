"""Test /v1/me quota block and admin quota endpoints."""
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.v1 import db, auth, routes
from app.core import config as cfg


def _setup(tmp_path, monkeypatch):
    """Initialize test app, db, user, and api key."""
    monkeypatch.setattr(cfg, "API_DB_PATH", str(tmp_path / "q.db"))
    monkeypatch.setattr(auth.config, "ADMIN_API_KEY", "admin-secret")
    c = db.connect(str(tmp_path / "q.db"))
    db.init_db(c)
    key, *_rest, uid = auth.mint_key(c, "u")
    app = FastAPI()
    app.state.queue = type("Q", (), {"enqueue": lambda s, j: 1, "depth": lambda s: 0})()
    app.include_router(routes.router)
    return TestClient(app), c, uid, key


def test_me_quota_block(tmp_path, monkeypatch):
    """GET /v1/me includes quota block with defaults."""
    tc, c, uid, key = _setup(tmp_path, monkeypatch)
    me = tc.get("/v1/me", headers={"Authorization": f"Bearer {key}"}).json()
    assert me["quota"]["daily_job_limit"] == cfg.API_DEFAULT_DAILY_JOB_LIMIT
    assert me["quota"]["max_in_flight"] == cfg.API_DEFAULT_MAX_IN_FLIGHT
    assert me["quota"]["daily_used"] == 0 and me["quota"]["in_flight"] == 0


def test_admin_set_and_clear_quota(tmp_path, monkeypatch):
    """Admin can set and clear quota; non-admin blocked."""
    tc, c, uid, key = _setup(tmp_path, monkeypatch)
    ah = {"Authorization": "Bearer admin-secret"}

    # Admin set quota
    resp = tc.post(
        f"/v1/admin/users/{uid}/quota",
        json={"daily_job_limit": 5, "max_in_flight": 2},
        headers=ah,
    )
    assert resp.status_code == 200

    # Verify quota was set
    assert db.get_quota(c, uid) == (5, 2)

    # Non-admin cannot set quota
    assert (
        tc.post(
            f"/v1/admin/users/{uid}/quota",
            json={"daily_job_limit": 1},
            headers={"Authorization": f"Bearer {key}"},
        ).status_code
        == 403
    )

    # Admin clear quota
    assert tc.delete(f"/v1/admin/users/{uid}/quota", headers=ah).status_code == 200

    # Verify quota reverted to defaults
    assert db.get_quota(c, uid) == (
        cfg.API_DEFAULT_DAILY_JOB_LIMIT,
        cfg.API_DEFAULT_MAX_IN_FLIGHT,
    )
