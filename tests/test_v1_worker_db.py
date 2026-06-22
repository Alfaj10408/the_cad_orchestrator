# tests/test_v1_worker_db.py
import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
from datetime import datetime, timezone, timedelta
from app.v1 import db


def _iso(dt): return dt.isoformat()
def _now(): return datetime.now(timezone.utc)


def _fresh(tmp_path):
    p = str(tmp_path / "w.db")
    c = db.connect(p); db.init_db(c)
    return p, c


def test_schema_has_worker_columns(tmp_path):
    _p, c = _fresh(tmp_path)
    cols = {r[1] for r in c.execute("PRAGMA table_info(jobs)").fetchall()}
    assert {"claimed_by", "claimed_at", "lease_expires_at"} <= cols
    assert "reclaim_count" not in cols          # poison-job handling deferred
    c.close()


def test_init_db_idempotent(tmp_path):
    p, c = _fresh(tmp_path)
    db.init_db(c)        # second run must not raise (duplicate column swallowed)
    db.init_db(c)
    c.close()


def test_next_pending_and_count(tmp_path):
    _p, c = _fresh(tmp_path)
    db.insert_job(c, "j1", "u1", "p1", status="pending")
    db.insert_job(c, "j2", "u1", "p2", status="pending")
    db.update_job(c, "j2", status="running")
    assert db.count_pending(c) == 1
    assert db.next_pending(c)["job_id"] == "j1"
    c.close()


def test_claim_single_winner(tmp_path):
    p, c = _fresh(tmp_path)
    db.insert_job(c, "j1", "u1", "p1", status="pending")
    c2 = db.connect(p)
    a = db.claim_job(c, "j1", "workerA", lease_s=120)
    b = db.claim_job(c2, "j1", "workerB", lease_s=120)
    assert (a, b) == (True, False)            # exactly one winner
    row = db.get_job_row(c, "j1")
    assert row["status"] == "running" and row["claimed_by"] == "workerA"
    assert row["claimed_at"] is not None and row["lease_expires_at"] is not None
    c.close(); c2.close()


def test_claim_fails_if_not_pending(tmp_path):
    _p, c = _fresh(tmp_path)
    db.insert_job(c, "j1", "u1", "p1", status="running")
    assert db.claim_job(c, "j1", "w", lease_s=120) is False
    c.close()


def test_renew_lease_extends_only_for_owner(tmp_path):
    _p, c = _fresh(tmp_path)
    db.insert_job(c, "j1", "u1", "p1", status="pending")
    db.claim_job(c, "j1", "wA", lease_s=120)
    before = db.get_job_row(c, "j1")["lease_expires_at"]
    assert db.renew_lease(c, "j1", "wA", lease_s=600) is True
    after = db.get_job_row(c, "j1")["lease_expires_at"]
    assert after > before
    assert db.renew_lease(c, "j1", "wB", lease_s=600) is False   # not owner
    c.close()


def test_reclaim_expired_returns_to_pending_clears_claim(tmp_path):
    _p, c = _fresh(tmp_path)
    db.insert_job(c, "j1", "u1", "p1", status="pending")
    past = _iso(_now() - timedelta(seconds=10))
    db.claim_job(c, "j1", "wDead", lease_s=120)
    db.update_job(c, "j1", lease_expires_at=past)        # force expiry
    n = db.reclaim_expired(c)
    assert n == 1
    row = db.get_job_row(c, "j1")
    assert row["status"] == "pending"                    # reclaimable, NOT failed
    assert row["claimed_by"] is None and row["claimed_at"] is None
    assert row["lease_expires_at"] is None
    c.close()


def test_reclaim_never_fails_a_job(tmp_path):
    _p, c = _fresh(tmp_path)
    db.insert_job(c, "j1", "u1", "p1", status="pending")
    past = _iso(_now() - timedelta(seconds=10))
    db.claim_job(c, "j1", "wDead", lease_s=120)
    db.update_job(c, "j1", lease_expires_at=past)
    db.reclaim_expired(c)                                 # repeated expiry never fails
    db.claim_job(c, "j1", "wDead2", lease_s=120)
    db.update_job(c, "j1", lease_expires_at=past)
    db.reclaim_expired(c)
    assert db.get_job_row(c, "j1")["status"] == "pending" # still reclaimable, never failed
    c.close()


def test_reclaim_does_not_touch_unexpired(tmp_path):
    _p, c = _fresh(tmp_path)
    db.insert_job(c, "j1", "u1", "p1", status="pending")
    db.claim_job(c, "j1", "wLive", lease_s=600)          # far-future lease
    assert db.reclaim_expired(c) == 0
    assert db.get_job_row(c, "j1")["status"] == "running"
    c.close()


def test_reclaim_treats_null_lease_running_as_expired(tmp_path):
    _p, c = _fresh(tmp_path)
    db.insert_job(c, "j1", "u1", "p1", status="running")  # legacy running, no lease
    assert db.reclaim_expired(c) == 1
    assert db.get_job_row(c, "j1")["status"] == "pending"
    c.close()
