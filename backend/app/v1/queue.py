"""In-process FIFO job queue with a single worker. Wraps claude_generation.run."""
from __future__ import annotations
import asyncio, json
import logging
_log = logging.getLogger("app.v1.queue")
from datetime import datetime, timezone
from app.core import config
from app.services import claude_generation, job_service
from app.v1 import db

_TERMINAL = {"COMPLETED": ("completed", None),
             "FAILED_CAD": ("failed", "cad"), "FAILED_QUOTA": ("failed", "quota"),
             "FAILED_TURNS": ("failed", "turns"), "CANCELLED": ("cancelled", None)}

def _now(): return datetime.now(timezone.utc).isoformat()

def _load_metrics(project_id):
    p = config.PROJECTS_ROOT / project_id / "reports" / "component_metrics.json"
    try: return p.read_text()
    except Exception: return None

def _terminal(conn, job_id, project_id, status, failure_class=None, stage=None):
    """Single terminal write: always capture metrics_json + failure_class."""
    db.update_job(conn, job_id, status=status, failure_class=failure_class,
                  stage=stage, completed_at=_now(),
                  metrics_json=_load_metrics(project_id))

class JobQueue:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or config.API_DB_PATH
        self.mode = config.API_WORKER_MODE
        self.worker_id = config.WORKER_ID
        self._q: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._conn = None

    def start(self):
        if self._task is None:
            self._conn = db.connect(self.db_path)
            if self.mode == "claim":
                self._task = asyncio.create_task(self._claim_worker())
            else:
                self._task = asyncio.create_task(self._worker())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try: await self._task
            except asyncio.CancelledError: pass
            self._task = None
        if self._conn is not None:
            self._conn.close(); self._conn = None

    def depth(self) -> int:
        if self.mode == "claim":
            c = db.connect(self.db_path)
            try: return db.count_pending(c)
            finally: c.close()
        return self._q.qsize()

    def alive(self):
        return self._task is not None and not self._task.done()

    def enqueue(self, job_id: str) -> int:
        if self.mode == "claim":
            c = db.connect(self.db_path)
            try: n = db.count_pending(c)
            finally: c.close()
            if n > config.API_MAX_QUEUE_DEPTH:
                raise RuntimeError("queue full")
            return n                       # row already inserted as pending; worker polls
        if self._q.qsize() >= config.API_MAX_QUEUE_DEPTH:
            raise RuntimeError("queue full")
        self._q.put_nowait(job_id)
        return self._q.qsize()

    def recover(self):
        conn = db.connect(self.db_path)
        try:
            if self.mode == "claim":
                n = db.reclaim_expired(conn)
                _log.info("reclaim worker=%s count=%d", self.worker_id, n)
            else:
                for r in db.list_running_jobs(conn):
                    _terminal(conn, r["job_id"], r["project_id"], "failed",
                              failure_class="internal")
                for r in db.list_pending_jobs(conn):
                    self._q.put_nowait(r["job_id"])
        finally:
            conn.close()

    async def _worker(self):
        while True:
            job_id = await self._q.get()
            row = db.get_job_row(self._conn, job_id)
            if row is None or row["status"] != "pending":
                continue
            db.update_job(self._conn, job_id, status="running", started_at=_now())
            try:
                await asyncio.wait_for(
                    claude_generation.run(row["project_id"], job_id),
                    timeout=config.JOB_WALLCLOCK_TIMEOUT)
            except asyncio.TimeoutError:
                _terminal(self._conn, job_id, row["project_id"], "failed",
                          failure_class="cad"); continue
            except Exception:  # noqa: BLE001
                _terminal(self._conn, job_id, row["project_id"], "failed",
                          failure_class="internal"); continue
            current = db.get_job_row(self._conn, job_id)
            if current is not None and current["status"] == "cancelled":
                _terminal(self._conn, job_id, row["project_id"], "cancelled"); continue
            j = job_service.get_job(job_id)
            status = getattr(j, "status", "FAILED_CAD")
            mapped, fclass = _TERMINAL.get(status, ("failed", "internal"))
            _terminal(self._conn, job_id, row["project_id"], mapped,
                      failure_class=fclass, stage=getattr(j, "stage", None))

    async def _heartbeat(self, job_id):
        try:
            while True:
                await asyncio.sleep(config.API_WORKER_HEARTBEAT_S)
                db.renew_lease(self._conn, job_id, self.worker_id,
                               lease_s=config.API_WORKER_LEASE_S)
                _log.info("renew worker=%s job=%s", self.worker_id, job_id)
        except asyncio.CancelledError:
            pass

    async def _claim_worker(self):
        while True:
            row = db.next_pending(self._conn)
            if row is None:
                await asyncio.sleep(config.API_WORKER_POLL_S)
                continue
            job_id, project_id = row["job_id"], row["project_id"]
            if not db.claim_job(self._conn, job_id, self.worker_id,
                                lease_s=config.API_WORKER_LEASE_S):
                continue                    # another worker won; poll again
            _log.info("claim worker=%s job=%s", self.worker_id, job_id)
            hb = asyncio.create_task(self._heartbeat(job_id))
            try:
                await asyncio.wait_for(
                    claude_generation.run(project_id, job_id),
                    timeout=config.JOB_WALLCLOCK_TIMEOUT)
            except asyncio.TimeoutError:
                hb.cancel()
                _terminal(self._conn, job_id, project_id, "failed", failure_class="cad")
                continue
            except Exception:  # noqa: BLE001
                hb.cancel()
                _terminal(self._conn, job_id, project_id, "failed", failure_class="internal")
                continue
            hb.cancel()
            current = db.get_job_row(self._conn, job_id)
            if current is not None and current["status"] == "cancelled":
                _terminal(self._conn, job_id, project_id, "cancelled"); continue
            j = job_service.get_job(job_id)
            status = getattr(j, "status", "FAILED_CAD")
            mapped, fclass = _TERMINAL.get(status, ("failed", "internal"))
            _terminal(self._conn, job_id, project_id, mapped,
                      failure_class=fclass, stage=getattr(j, "stage", None))
