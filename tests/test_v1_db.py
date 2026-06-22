import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
from app.v1 import db

def _conn(tmp_path):
    c = db.connect(str(tmp_path / "t.db")); db.init_db(c); return c

def test_user_key_job_roundtrip(tmp_path):
    c = _conn(tmp_path)
    uid = db.create_user(c, "alice", is_admin=False)
    assert db.get_user(c, uid)["name"] == "alice"
    kid = db.create_api_key(c, uid, key_hash="h1", key_prefix="sk_abc")
    assert db.get_key_by_hash(c, "h1")["user_id"] == uid
    db.insert_job(c, "j1", uid, "p1", status="pending")
    db.update_job(c, "j1", status="running", stage="COMPONENT_GENERATION")
    row = db.get_job_row(c, "j1")
    assert row["status"] == "running" and row["user_id"] == uid and row["project_id"] == "p1"
    assert [r["job_id"] for r in db.list_pending_jobs(c)] == []   # none pending now
    db.revoke_key(c, kid)
    assert db.get_key_by_hash(c, "h1") is None                    # revoked keys not returned
