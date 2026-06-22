import sys, importlib
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
import pytest
from fastapi.testclient import TestClient

def _load(monkeypatch, tmp_path, salt, admin):
    monkeypatch.setenv("API_DB_PATH", str(tmp_path/"a.db"))
    monkeypatch.setenv("API_KEY_SALT", salt)
    if admin is None: monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    else: monkeypatch.setenv("ADMIN_API_KEY", admin)
    from app.core import config as c; importlib.reload(c)
    from app import main as m; importlib.reload(m)
    return m

def test_default_salt_with_admin_refuses_boot(tmp_path, monkeypatch):
    m = _load(monkeypatch, tmp_path, "dev-salt-change-me", "admin-secret")
    with pytest.raises(Exception):
        with TestClient(m.app):   # lifespan startup raises
            pass

def test_strong_salt_boots(tmp_path, monkeypatch):
    m = _load(monkeypatch, tmp_path, "a-strong-unique-salt", "admin-secret")
    with TestClient(m.app) as tc:
        assert tc.get("/v1/healthz").status_code == 200

def test_dev_default_salt_no_admin_boots(tmp_path, monkeypatch):
    m = _load(monkeypatch, tmp_path, "dev-salt-change-me", None)
    with TestClient(m.app) as tc:
        assert tc.get("/v1/healthz").status_code == 200

def test_default_salt_with_admin_keys_refuses_boot(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEYS", "k1,k2")
    m = _load(monkeypatch, tmp_path, "dev-salt-change-me", "")
    with pytest.raises(Exception):
        with TestClient(m.app):   # lifespan startup raises
            pass
