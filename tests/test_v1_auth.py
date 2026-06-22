import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
import pytest
from fastapi import HTTPException, FastAPI, Depends
from fastapi.testclient import TestClient
from app.v1 import db, auth

def _conn(tmp_path):
    c = db.connect(str(tmp_path/"a.db")); db.init_db(c); return c

def test_hash_deterministic_and_prefix():
    k = auth.gen_key()
    assert k.startswith("sk_")
    assert auth.hash_key(k) == auth.hash_key(k)
    assert auth.hash_key(k) != auth.hash_key(auth.gen_key())

def test_resolve_user_from_key(tmp_path):
    c = _conn(tmp_path)
    key, prefix, kid, uid = auth.mint_key(c, "bob")
    assert auth._resolve_user(c, f"Bearer {key}") == uid
    with pytest.raises(HTTPException):
        auth._resolve_user(c, "Bearer sk_wrong")
    with pytest.raises(HTTPException):
        auth._resolve_user(c, None)

def test_admin_check(monkeypatch):
    monkeypatch.setattr(auth.config, "ADMIN_API_KEY", "admin-secret")
    assert auth._is_admin("Bearer admin-secret") is True
    assert auth._is_admin("Bearer nope") is False
    monkeypatch.setattr(auth.config, "ADMIN_API_KEY", "")
    assert auth._is_admin("Bearer admin-secret") is False   # empty admin key disables admin

def test_require_user_dep_uses_get_conn(tmp_path, monkeypatch):
    monkeypatch.setattr(auth.config, "API_DB_PATH", str(tmp_path / "ru.db"))
    c = db.connect(str(tmp_path / "ru.db")); db.init_db(c)
    key, *_ , uid = auth.mint_key(c, "u"); c.close()
    app = FastAPI()
    @app.get("/who")
    def who(user_id: str = Depends(auth.require_user)):
        return {"user_id": user_id}
    tc = TestClient(app)
    assert tc.get("/who", headers={"Authorization": f"Bearer {key}"}).json()["user_id"] == uid
    assert tc.get("/who").status_code == 401
