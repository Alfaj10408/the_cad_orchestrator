import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
from app.v1 import db

def _conn(tmp_path):
    c = db.connect(str(tmp_path / "qp.db")); db.init_db(c); return c

def _add(c, jid, status="pending"):
    db.insert_job(c, jid, "u1", "p_"+jid, status="pending")
    if status != "pending":
        db.update_job(c, jid, status=status)

def test_pending_position_ranks_by_created_at(tmp_path):
    c = _conn(tmp_path)
    for jid in ("a", "b", "c"):       # inserted in order → created_at ascending
        _add(c, jid)
    assert db.pending_position(c, "a") == 1
    assert db.pending_position(c, "b") == 2
    assert db.pending_position(c, "c") == 3

def test_non_pending_returns_none(tmp_path):
    c = _conn(tmp_path)
    _add(c, "a"); _add(c, "b")
    db.update_job(c, "a", status="running")     # a leaves pending set
    assert db.pending_position(c, "a") is None   # not pending → None
    assert db.pending_position(c, "b") == 1      # b shifts to 1
    assert db.pending_position(c, "missing") is None
