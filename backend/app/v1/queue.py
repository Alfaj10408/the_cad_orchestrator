"""In-process FIFO job queue with a single worker. Wraps claude_generation.run."""
from __future__ import annotations
import asyncio, json
from datetime import datetime, timezone
from app.core import config, paths
from app.services import claude_generation, job_service
from app.v1 import db

_TERMINAL = {"COMPLETED": ("completed", None),
             "FAILED_CAD": ("failed", "cad"), "FAILED_QUOTA": ("failed", "quota"),
             "FAILED_TURNS": ("failed", "turns"), "CANCELLED": ("cancelled", None)}

def _now(): return datetime.now(timezone.utc).isoformat()

def _load_metrics(project_id):
    p = paths.project_dir(project_id) / "reports" / "component_metrics.json"
    try: return p.read_text()
    except Exception: return None

class JobQueue:
    def __init__(self, conn):
        self.conn = conn
        self._q: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._worker())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try: await self._task
            except asyncio.CancelledError: pass
            self._task = None

    def depth(self) -> int:
        return self._q.qsize()

    def enqueue(self, job_id: str) -> int:
        if self._q.qsize() >= config.API_MAX_QUEUE_DEPTH:
            raise RuntimeError("queue full")
        self._q.put_nowait(job_id)
        return self._q.qsize()

    def recover(self, conn):
        # running jobs with no live worker (post-restart) -> failed/internal
        for r in db.list_running_jobs(conn):
            db.update_job(conn, r["job_id"], status="failed", failure_class="internal",
                          completed_at=_now())
        for r in db.list_pending_jobs(conn):
            self._q.put_nowait(r["job_id"])

    async def _worker(self):
        while True:
            job_id = await self._q.get()
            row = db.get_job_row(self.conn, job_id)
            if row is None or row["status"] != "pending":
                continue
            db.update_job(self.conn, job_id, status="running", started_at=_now())
            try:
                await asyncio.wait_for(
                    claude_generation.run(row["project_id"], job_id),
                    timeout=config.JOB_WALLCLOCK_TIMEOUT)
            except asyncio.TimeoutError:
                db.update_job(self.conn, job_id, status="failed", failure_class="cad",
                              completed_at=_now()); continue
            except Exception:  # noqa: BLE001
                db.update_job(self.conn, job_id, status="failed", failure_class="internal",
                              completed_at=_now()); continue
            j = job_service.get_job(job_id)
            status = getattr(j, "status", "FAILED_CAD")
            mapped, fclass = _TERMINAL.get(status, ("failed", "internal"))
            db.update_job(self.conn, job_id, status=mapped, failure_class=fclass,
                          stage=getattr(j, "stage", None), completed_at=_now(),
                          metrics_json=_load_metrics(row["project_id"]))
