# tests/test_v1_admin_keys_api.py
import sys, hashlib
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
    monkeypatch.setattr(cfg, "ADMIN_API_KEY", "")
    monkeypatch.setattr(cfg, "ADMIN_API_KEYS", "adminA,adminB")
    monkeypatch.setattr(cfg, "ADMIN_KEYS_INFO_ENABLED", True)
    c = db.connect(str(tmp_path / "r.db")); db.init_db(c)
    key, _p, _k, _u = auth.mint_key(c, "u1")   # normal user
    c.commit(); c.close()
    app = FastAPI(); app.include_router(routes.router)
    tc = TestClient(app); tc._ukey = key
    return tc


def test_info_count_and_fingerprint(client):
    r = client.get("/v1/admin/keys/info", headers={"Authorization": "Bearer adminA"})
    assert r.status_code == 200
    body = r.json()
    assert body["admin_keys_configured"] == 2
    assert body["authenticated_fingerprint"] == hashlib.sha256(b"adminA").hexdigest()[:8]


def test_info_fingerprint_differs_per_key(client):
    r = client.get("/v1/admin/keys/info", headers={"Authorization": "Bearer adminB"})
    assert r.json()["authenticated_fingerprint"] == hashlib.sha256(b"adminB").hexdigest()[:8]


def test_info_requires_admin(client):
    r = client.get("/v1/admin/keys/info", headers={"Authorization": f"Bearer {client._ukey}"})
    assert r.status_code == 403


def test_info_disabled_404(client, monkeypatch):
    monkeypatch.setattr(cfg, "ADMIN_KEYS_INFO_ENABLED", False)
    r = client.get("/v1/admin/keys/info", headers={"Authorization": "Bearer adminA"})
    assert r.status_code == 404
    # still admin-gated first: non-admin gets 403 even when disabled
    r2 = client.get("/v1/admin/keys/info", headers={"Authorization": f"Bearer {client._ukey}"})
    assert r2.status_code == 403


def test_info_overlap_both_keys_work(client):
    for k in ("adminA", "adminB"):
        assert client.get("/v1/admin/keys/info",
                          headers={"Authorization": f"Bearer {k}"}).status_code == 200
