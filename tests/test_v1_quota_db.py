import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
from app.v1 import db
from app.core import config as cfg

def _c(tmp): c = db.connect(str(tmp/"q.db")); db.init_db(c); return c

def test_counts(tmp_path):
    c = _c(tmp_path)
    db.insert_job(c, "j1", "u1", "p1", status="pending")
    db.insert_job(c, "j2", "u1", "p2", status="running")
    db.insert_job(c, "j3", "u1", "p3", status="completed")
    db.insert_job(c, "j4", "u2", "p4", status="pending")
    assert db.count_in_flight(c, "u1") == 2          # pending+running
    assert db.count_created_since(c, "u1", "0000") == 3   # all u1 jobs after epoch-ish
    assert db.count_created_since(c, "u1", "9999") == 0

def test_quota_default_and_override(tmp_path):
    c = _c(tmp_path)
    assert db.get_quota(c, "u1") == (cfg.API_DEFAULT_DAILY_JOB_LIMIT, cfg.API_DEFAULT_MAX_IN_FLIGHT)
    db.set_quota(c, "u1", 10, 1)
    assert db.get_quota(c, "u1") == (10, 1)
    db.set_quota(c, "u1", None, 2)                   # partial: daily falls back to default
    assert db.get_quota(c, "u1") == (cfg.API_DEFAULT_DAILY_JOB_LIMIT, 2)
    db.clear_quota(c, "u1")
    assert db.get_quota(c, "u1") == (cfg.API_DEFAULT_DAILY_JOB_LIMIT, cfg.API_DEFAULT_MAX_IN_FLIGHT)
