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

def test_artifact_download_f1(tmp_path):
    """F1 fix: artifacts outside cad/ subdir must be downloadable by relative_path."""
    import sys
    _BACKEND = Path(__file__).resolve().parents[1] / "backend"
    if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
    from app.core import paths as core_paths
    import app.core.paths as _paths_mod

    app, conn = _app(tmp_path)
    c = TestClient(app)

    # Redirect project_dir to tmp_path so files land somewhere controlled
    real_project_dir = core_paths.project_dir

    key, _, _, uid = auth.mint_key(conn, "owner")
    h = {"Authorization": f"Bearer {key}"}

    # Create a job pointing to a known project_id
    r = c.post("/v1/jobs", json={"prompt": "a test block"}, headers=h)
    assert r.status_code == 201
    jid = r.json()["job_id"]

    # Find the project_id via the DB
    row = db.get_job_row(conn, jid)
    pid = row["project_id"]

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
    key2, _, _, _ = auth.mint_key(conn, "other")
    h2 = {"Authorization": f"Bearer {key2}"}
    assert c.get(f"/v1/jobs/{jid}/artifacts/cad/model.step", headers=h2).status_code == 404
