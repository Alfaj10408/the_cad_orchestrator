import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.v1 import db, auth, routes
from app.core import config as _config

def _app(tmp_path, monkeypatch):
    db_path = str(tmp_path / "r.db")
    monkeypatch.setattr(_config, "API_DB_PATH", db_path)
    # db.connect() reads config.API_DB_PATH at call time; also patch it in db's module view
    import app.v1.db as _db_mod
    monkeypatch.setattr(_db_mod.config, "API_DB_PATH", db_path)
    # Init schema once
    init_conn = db.connect(db_path); db.init_db(init_conn); init_conn.close()

    class _Q:
        def __init__(s): s.enqueued=[]
        def enqueue(s, jid): s.enqueued.append(jid); return len(s.enqueued)
        def depth(s): return 0
    app = FastAPI()
    app.state.queue = _Q()
    app.include_router(routes.router)
    return app

def test_create_and_get_job_requires_auth(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    db_path = str(tmp_path / "r.db")
    c = TestClient(app)
    assert c.post("/v1/jobs", json={"prompt":"a block"}).status_code == 401
    mint_conn = db.connect(db_path)
    key,_,_,uid = auth.mint_key(mint_conn, "u")
    key2,_,_,_ = auth.mint_key(mint_conn, "u2")
    mint_conn.close()
    h = {"Authorization": f"Bearer {key}"}
    r = c.post("/v1/jobs", json={"prompt":"a 40x30x10 block"}, headers=h)
    assert r.status_code == 201
    jid = r.json()["job_id"]
    assert app.state.queue.enqueued == [jid]
    s = c.get(f"/v1/jobs/{jid}", headers=h)
    assert s.status_code == 200 and s.json()["status"] == "pending"
    # ownership: another user cannot see it
    assert c.get(f"/v1/jobs/{jid}", headers={"Authorization":f"Bearer {key2}"}).status_code == 404
    # /v1/me returns the authenticated user
    me = c.get("/v1/me", headers=h)
    assert me.status_code == 200 and me.json()["user_id"] == uid and me.json()["name"] == "u"
    assert c.get("/v1/me").status_code == 401  # unauth

def test_artifact_download_f1(tmp_path, monkeypatch):
    """F1 fix: artifacts outside cad/ subdir must be downloadable by relative_path."""
    import sys
    _BACKEND = Path(__file__).resolve().parents[1] / "backend"
    if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
    from app.core import paths as core_paths
    import app.v1.db as _db_mod

    db_path = str(tmp_path / "r.db")
    monkeypatch.setattr(_config, "API_DB_PATH", db_path)
    monkeypatch.setattr(_db_mod.config, "API_DB_PATH", db_path)
    init_conn = db.connect(db_path); db.init_db(init_conn); init_conn.close()

    class _Q:
        def __init__(s): s.enqueued=[]
        def enqueue(s, jid): s.enqueued.append(jid); return len(s.enqueued)
        def depth(s): return 0
    app = FastAPI()
    app.state.queue = _Q()
    app.include_router(routes.router)
    c = TestClient(app)

    mint_conn = db.connect(db_path)
    key, _, _, uid = auth.mint_key(mint_conn, "owner")
    key2, _, _, _ = auth.mint_key(mint_conn, "other")
    mint_conn.close()
    h = {"Authorization": f"Bearer {key}"}

    # Create a job pointing to a known project_id
    r = c.post("/v1/jobs", json={"prompt": "a test block"}, headers=h)
    assert r.status_code == 201
    jid = r.json()["job_id"]

    # Find the project_id via the DB
    read_conn = db.connect(db_path)
    row = db.get_job_row(read_conn, jid)
    pid = row["project_id"]
    read_conn.close()

    # Create real files in the project tree
    proj_root = core_paths.project_dir(pid)
    for rel in ("cad/model.step", "meshes/model.stl", "reports/inspection.txt"):
        p = proj_root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"dummy {rel}")

    # F1 core: cad/ artifact
    assert c.get(f"/v1/jobs/{jid}/artifacts/cad/model.step", headers=h).status_code == 200
    # F1 regression fix: meshes/
    assert c.get(f"/v1/jobs/{jid}/artifacts/meshes/model.stl", headers=h).status_code == 200
    # F1 regression fix: reports/
    assert c.get(f"/v1/jobs/{jid}/artifacts/reports/inspection.txt", headers=h).status_code == 200

    # Traversal must be rejected
    assert c.get(f"/v1/jobs/{jid}/artifacts/../../etc/passwd", headers=h).status_code == 404

    # Cross-user ownership: different key cannot download
    h2 = {"Authorization": f"Bearer {key2}"}
    assert c.get(f"/v1/jobs/{jid}/artifacts/cad/model.step", headers=h2).status_code == 404
