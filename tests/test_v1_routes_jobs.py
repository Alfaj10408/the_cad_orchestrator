import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.v1 import db, auth, routes

def _app(tmp_path):
    app = FastAPI()
    conn = db.connect(str(tmp_path/"r.db")); db.init_db(conn)
    class _Q:
        def __init__(s): s.enqueued=[]
        def enqueue(s, jid): s.enqueued.append(jid); return len(s.enqueued)
        def depth(s): return 0
    app.state.db = conn; app.state.queue = _Q()
    app.include_router(routes.router)
    return app, conn

def test_create_and_get_job_requires_auth(tmp_path):
    app, conn = _app(tmp_path)
    c = TestClient(app)
    assert c.post("/v1/jobs", json={"prompt":"a block"}).status_code == 401
    key,_,_,uid = auth.mint_key(conn, "u")
    h = {"Authorization": f"Bearer {key}"}
    r = c.post("/v1/jobs", json={"prompt":"a 40x30x10 block"}, headers=h)
    assert r.status_code == 201
    jid = r.json()["job_id"]
    assert app.state.queue.enqueued == [jid]
    s = c.get(f"/v1/jobs/{jid}", headers=h)
    assert s.status_code == 200 and s.json()["status"] == "pending"
    # ownership: another user cannot see it
    key2,_,_,_ = auth.mint_key(conn, "u2")
    assert c.get(f"/v1/jobs/{jid}", headers={"Authorization":f"Bearer {key2}"}).status_code == 404
    # /v1/me returns the authenticated user
    me = c.get("/v1/me", headers=h)
    assert me.status_code == 200 and me.json()["user_id"] == uid and me.json()["name"] == "u"
    assert c.get("/v1/me").status_code == 401  # unauth
