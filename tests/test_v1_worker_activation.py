# tests/test_v1_worker_activation.py
import sys, asyncio, logging
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


def test_two_workers_no_duplicate_execution(tmp_path, monkeypatch):
    _claim_env(monkeypatch)
    db_path = str(tmp_path / "q.db")
    c = db.connect(db_path); db.init_db(c)
    db.insert_job(c, "j1", "u1", "p1", status="pending"); c.close()
    calls = []
    async def fake_run(project_id, job_id): calls.append(job_id); await asyncio.sleep(0.05)
    monkeypatch.setattr(q.claude_generation, "run", fake_run)
    monkeypatch.setattr(q.job_service, "get_job", lambda jid: _Job("COMPLETED"))
    jq1 = q.JobQueue(db_path); jq2 = q.JobQueue(db_path)   # two workers, shared DB
    async def drive():
        jq1.start(); jq2.start()
        chk = db.connect(db_path)
        for _ in range(100):
            if db.get_job_row(chk, "j1")["status"] == "completed": break
            await asyncio.sleep(0.02)
        chk.close()
        await jq1.stop(); await jq2.stop()
    asyncio.run(drive())
    assert calls.count("j1") == 1            # ran exactly once across both workers


def test_two_workers_each_job_runs_once(tmp_path, monkeypatch):
    _claim_env(monkeypatch)
    db_path = str(tmp_path / "m.db")
    c = db.connect(db_path); db.init_db(c)
    for i in range(6):
        db.insert_job(c, f"j{i}", "u1", f"p{i}", status="pending")
    c.close()
    calls = []
    async def fake_run(project_id, job_id): calls.append(job_id); await asyncio.sleep(0.01)
    monkeypatch.setattr(q.claude_generation, "run", fake_run)
    monkeypatch.setattr(q.job_service, "get_job", lambda jid: _Job("COMPLETED"))
    jq1 = q.JobQueue(db_path); jq2 = q.JobQueue(db_path)
    async def drive():
        jq1.start(); jq2.start()
        chk = db.connect(db_path)
        for _ in range(200):
            if db.count_pending(chk) == 0 and not db.active_claims(chk): break
            await asyncio.sleep(0.02)
        chk.close()
        await jq1.stop(); await jq2.stop()
    asyncio.run(drive())
    assert sorted(calls) == [f"j{i}" for i in range(6)]    # each once, none twice


def test_claim_emits_log(tmp_path, monkeypatch, caplog):
    _claim_env(monkeypatch)
    db_path = str(tmp_path / "l.db")
    c = db.connect(db_path); db.init_db(c)
    db.insert_job(c, "j1", "u1", "p1", status="pending"); c.close()
    async def fake_run(project_id, job_id): pass
    monkeypatch.setattr(q.claude_generation, "run", fake_run)
    monkeypatch.setattr(q.job_service, "get_job", lambda jid: _Job("COMPLETED"))
    jq = q.JobQueue(db_path)
    async def drive():
        jq.start(); chk = db.connect(db_path)
        for _ in range(100):
            if db.get_job_row(chk, "j1")["status"] == "completed": break
            await asyncio.sleep(0.02)
        chk.close(); await jq.stop()
    with caplog.at_level(logging.INFO, logger="app.v1.queue"):
        asyncio.run(drive())
    assert any("claim" in r.getMessage() for r in caplog.records)


def test_recover_reclaim_logs_and_requeues(tmp_path, monkeypatch, caplog):
    _claim_env(monkeypatch)
    db_path = str(tmp_path / "rc.db")
    c = db.connect(db_path); db.init_db(c)
    db.insert_job(c, "j1", "u1", "p1", status="running")   # NULL-lease running -> reclaimable
    c.close()
    jq = q.JobQueue(db_path)
    with caplog.at_level(logging.INFO, logger="app.v1.queue"):
        jq.recover()
    chk = db.connect(db_path)
    assert db.get_job_row(chk, "j1")["status"] == "pending"
    chk.close()
    assert any("reclaim" in r.getMessage() for r in caplog.records)
