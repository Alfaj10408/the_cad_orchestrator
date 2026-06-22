import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.v1 import db, auth, routes
from app.core import config as _config

def test_healthz_unauth(tmp_path, monkeypatch):
    db_path = str(tmp_path / "h.db")
    monkeypatch.setattr(_config, "API_DB_PATH", db_path)
    import app.v1.db as _db_mod
    monkeypatch.setattr(_db_mod.config, "API_DB_PATH", db_path)
    init_conn = db.connect(db_path); db.init_db(init_conn); init_conn.close()

    app = FastAPI()
    class _Q:  # readyz checks worker liveness
        def alive(s): return True
        def depth(s): return 0
    app.state.queue = _Q()
    app.include_router(routes.router)
    c = TestClient(app)
    assert c.get("/v1/healthz").json()["ok"] is True
    r = c.get("/v1/readyz").json()
    assert "checks" in r and "ready" in r
