# tests/test_v1_queue.py
import sys, asyncio
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
from app.v1 import db, queue as q

class _Job:  # stand-in for job_service.Job
    def __init__(self, status): self.status=status; self.stage=status

def test_queue_runs_and_marks_completed(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path/"q.db")); db.init_db(conn)
    db.insert_job(conn, "j1", "u1", "p1", status="pending")
    ran = {}
    async def fake_run(project_id, job_id): ran["called"]=(project_id,job_id)
    monkeypatch.setattr(q.claude_generation, "run", fake_run)
    monkeypatch.setattr(q.job_service, "get_job", lambda jid: _Job("COMPLETED"))
    jq = q.JobQueue(conn)
    async def drive():
        jq.start(); jq.enqueue("j1")
        for _ in range(50):
            if db.get_job_row(conn,"j1")["status"]=="completed": break
            await asyncio.sleep(0.02)
        await jq.stop()
    asyncio.run(drive())
    assert ran["called"]==("p1","j1")
    assert db.get_job_row(conn,"j1")["status"]=="completed"

def test_recover_marks_running_as_failed(tmp_path):
    conn = db.connect(str(tmp_path/"r.db")); db.init_db(conn)
    db.insert_job(conn,"jr","u1","p1",status="pending"); db.update_job(conn,"jr",status="running")
    jq = q.JobQueue(conn); jq.recover(conn)
    row=db.get_job_row(conn,"jr")
    assert row["status"]=="failed" and row["failure_class"]=="internal"

def test_queue_does_not_overwrite_cancelled_job(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path/"c.db")); db.init_db(conn)
    db.insert_job(conn, "j2", "u1", "p1", status="pending")
    ran = {}
    async def fake_run(project_id, job_id):
        ran["called"]=(project_id,job_id)
        # simulate user cancelling mid-run: set job to cancelled before run completes
        db.update_job(conn, job_id, status="cancelled")
    monkeypatch.setattr(q.claude_generation, "run", fake_run)
    # job_service returns COMPLETED, but we should NOT use it if already cancelled
    monkeypatch.setattr(q.job_service, "get_job", lambda jid: _Job("COMPLETED"))
    jq = q.JobQueue(conn)
    async def drive():
        jq.start(); jq.enqueue("j2")
        for _ in range(50):
            row = db.get_job_row(conn,"j2")
            if row["status"]=="cancelled": break
            await asyncio.sleep(0.02)
        await jq.stop()
    asyncio.run(drive())
    assert ran["called"]==("p1","j2")
    # assert job stays cancelled, NOT overwritten to completed
    assert db.get_job_row(conn,"j2")["status"]=="cancelled"
