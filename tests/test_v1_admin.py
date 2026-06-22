import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.v1 import db, auth, routes

def test_admin_mint_key(tmp_path, monkeypatch):
    monkeypatch.setattr(auth.config, "ADMIN_API_KEY", "admin-secret")
    app = FastAPI(); conn = db.connect(str(tmp_path/"ad.db")); db.init_db(conn)
    app.state.db = conn; app.state.queue = type("Q",(),{"enqueue":lambda s,j:1,"depth":lambda s:0})()
    app.include_router(routes.router)
    c = TestClient(app)
    assert c.post("/v1/admin/keys", json={"user_name":"x"}).status_code == 403  # no admin
    r = c.post("/v1/admin/keys", json={"user_name":"x"},
               headers={"Authorization":"Bearer admin-secret"})
    assert r.status_code == 201 and r.json()["key"].startswith("sk_")
    # minted key works as a user
    key = r.json()["key"]
    assert c.post("/v1/jobs", json={"prompt":"a block"},
                  headers={"Authorization":f"Bearer {key}"}).status_code == 201
