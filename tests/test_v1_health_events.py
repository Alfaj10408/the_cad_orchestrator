import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.v1 import db, auth, routes

def test_healthz_unauth(tmp_path):
    app = FastAPI(); conn = db.connect(str(tmp_path/"h.db")); db.init_db(conn)
    app.state.db = conn
    class _Q:  # readyz checks worker liveness
        def alive(s): return True
        def depth(s): return 0
    app.state.queue = _Q()
    app.include_router(routes.router)
    c = TestClient(app)
    assert c.get("/v1/healthz").json()["ok"] is True
    r = c.get("/v1/readyz").json()
    assert "checks" in r and "ready" in r
