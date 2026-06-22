# tests/test_v1_worker_claim.py
import sys, asyncio
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
from app.v1 import db, queue as q
from app.core import config as cfg


class _Job:
    def __init__(self, status): self.status = status; self.stage = status


def _claim_env(monkeypatch):
    monkeypatch.setattr(cfg, "API_WORKER_MODE", "claim")
    monkeypatch.setattr(cfg, "API_WORKER_POLL_S", 0.02)
    monkeypatch.setattr(cfg, "API_WORKER_LEASE_S", 120)
    monkeypatch.setattr(cfg, "API_WORKER_HEARTBEAT_S", 0.02)


def test_claim_mode_runs_job_to_completed(tmp_path, monkeypatch):
    _claim_env(monkeypatch)
    db_path = str(tmp_path / "q.db")
    c = db.connect(db_path); db.init_db(c)
    db.insert_job(c, "j1", "u1", "p1", status="pending"); c.close()
    ran = {}
    async def fake_run(project_id, job_id): ran["x"] = (project_id, job_id)
    monkeypatch.setattr(q.claude_generation, "run", fake_run)
    monkeypatch.setattr(q.job_service, "get_job", lambda jid: _Job("COMPLETED"))
    jq = q.JobQueue(db_path)
    async def drive():
        jq.start()
        chk = db.connect(db_path)
        for _ in range(100):
            if db.get_job_row(chk, "j1")["status"] == "completed": break
            await asyncio.sleep(0.02)
        chk.close(); await jq.stop()
    asyncio.run(drive())
    assert ran["x"] == ("p1", "j1")
    chk = db.connect(db_path)
    row = db.get_job_row(chk, "j1")
    assert row["status"] == "completed" and row["claimed_by"]  # was claimed
    chk.close()


def test_claim_mode_recover_reclaims_not_failall(tmp_path, monkeypatch):
    _claim_env(monkeypatch)
    db_path = str(tmp_path / "r.db")
    c = db.connect(db_path); db.init_db(c)
    db.insert_job(c, "j1", "u1", "p1", status="running")   # legacy running, no lease
    c.close()
    jq = q.JobQueue(db_path); jq.recover()                 # claim mode -> reclaim
    chk = db.connect(db_path)
    assert db.get_job_row(chk, "j1")["status"] == "pending"  # NOT failed
    chk.close()


def test_single_mode_recover_still_failall(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "API_WORKER_MODE", "single")
    db_path = str(tmp_path / "s.db")
    c = db.connect(db_path); db.init_db(c)
    db.insert_job(c, "j1", "u1", "p1", status="running"); c.close()
    jq = q.JobQueue(db_path); jq.recover()
    chk = db.connect(db_path)
    assert db.get_job_row(chk, "j1")["status"] == "failed"   # current behavior
    chk.close()


def test_depth_claim_mode_counts_pending(tmp_path, monkeypatch):
    _claim_env(monkeypatch)
    db_path = str(tmp_path / "d.db")
    c = db.connect(db_path); db.init_db(c)
    db.insert_job(c, "j1", "u1", "p1", status="pending")
    db.insert_job(c, "j2", "u1", "p2", status="pending"); c.close()
    jq = q.JobQueue(db_path)
    assert jq.depth() == 2
