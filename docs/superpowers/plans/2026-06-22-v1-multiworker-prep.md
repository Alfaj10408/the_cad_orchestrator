# Multi-Worker Preparation (P2) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Build a flag-gated DB-backed claim/lease/heartbeat foundation so N worker processes (one host, shared SQLite) are safe, while `API_WORKER_MODE="single"` (default) keeps today's exact behavior. First additive, gated schema change.

**Architecture:** `db.py` gains four additive `jobs` columns (idempotent ALTER) + atomic-claim/lease/reclaim helpers. `queue.py` gains a second worker path (poll → atomic claim → run → heartbeat → terminal) and lease-scoped recovery, both behind `config.API_WORKER_MODE`; the single-mode path is byte-for-byte unchanged. No engine/main.py changes (recover/start branch internally).

**Tech Stack:** Python 3.11 (`/root/anaconda3/envs/cadskills/bin/python`), sqlite3 (WAL), asyncio, pytest (no pytest-asyncio — drive via `asyncio.run`).

## Global Constraints
- Engine frozen: **never** modify `backend/app/services` or `backend/app/orchestrator` — STOP/BLOCKED otherwise. Guard `git diff v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator` must stay empty.
- No CAD/frontend/benchmark changes. **Multi-worker disabled by default** — `API_WORKER_MODE="single"` is byte-for-byte current behavior; all new logic behind the flag.
- Edits ONLY in `backend/app/v1/db.py`, `backend/app/v1/queue.py`, `backend/app/core/config.py`, `tests/test_v1_*`. (main.py unchanged — `recover()`/`start()` branch internally.)
- Schema: additive only — idempotent `ALTER TABLE jobs ADD COLUMN` for `claimed_by TEXT`, `claimed_at TEXT`, `lease_expires_at TEXT` (exactly these three). No new tables, no data migration. Old rows → NULL.
- **Lease expiry makes a job RECLAIMABLE (→ pending), never directly failed.** Reclaim clears `claimed_by`/`claimed_at`/`lease_expires_at`. No cap, no fail branch, no poison-job handling in this milestone (deferred to roadmap).
- Atomic claim = single-winner `UPDATE ... WHERE status='pending'` (`cursor.rowcount==1` wins).
- Run from product root with cadskills python. Guard-check each commit.

---

## Task 1 — schema (additive ALTERs) + claim/lease db helpers

**Files:**
- Modify: `backend/app/v1/db.py`
- Test: `tests/test_v1_worker_db.py`

**Interfaces — Produces:**
- `init_db` adds the 3 columns (`claimed_by`, `claimed_at`, `lease_expires_at`) idempotently via `_add_column_if_missing(conn, table, name, decl)`.
- `db.next_pending(conn) -> sqlite3.Row | None` (oldest pending by created_at, job_id).
- `db.count_pending(conn) -> int`.
- `db.claim_job(conn, job_id, worker_id, *, lease_s, now=None) -> bool` (atomic; True iff this worker won).
- `db.renew_lease(conn, job_id, worker_id, *, lease_s, now=None) -> bool` (extends lease iff owned).
- `db.reclaim_expired(conn, *, now=None) -> int` (count reclaimed → `pending`; no fail branch).

- [ ] **Step 1: Write the failing test**
```python
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
```

- [ ] **Step 2: Run, verify fail** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_worker_db.py -v`
Expected: FAIL (missing columns / helpers).

- [ ] **Step 3: Implement schema + helpers** — in `backend/app/v1/db.py`:

(a) add the idempotent column helper + calls inside `init_db` (after the existing `CREATE TABLE` statements run; use the same `conn`):
```python
def _add_column_if_missing(conn, table: str, name: str, decl: str) -> None:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if name not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
```
At the end of `init_db` (before any final `conn.commit()` / return), add:
```python
    _add_column_if_missing(conn, "jobs", "claimed_by", "TEXT")
    _add_column_if_missing(conn, "jobs", "claimed_at", "TEXT")
    _add_column_if_missing(conn, "jobs", "lease_expires_at", "TEXT")
    conn.commit()
```
(If `init_db` already ends with a commit, keep one commit after the ALTERs.)

(b) add the helpers (near the other job helpers). Use UTC ISO timestamps consistent with `_now()`:
```python
from datetime import datetime, timezone, timedelta   # ensure imported at top

def _utc_now():
    return datetime.now(timezone.utc)

def next_pending(conn):
    return conn.execute(
        "SELECT * FROM jobs WHERE status='pending' "
        "ORDER BY created_at, job_id LIMIT 1").fetchone()

def count_pending(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS c FROM jobs WHERE status='pending'").fetchone()["c"]

def claim_job(conn, job_id, worker_id, *, lease_s, now=None) -> bool:
    n = now or _utc_now()
    started = n.isoformat()
    lease = (n + timedelta(seconds=lease_s)).isoformat()
    cur = conn.execute(
        "UPDATE jobs SET status='running', started_at=?, claimed_by=?, "
        "claimed_at=?, lease_expires_at=? WHERE job_id=? AND status='pending'",
        (started, worker_id, started, lease, job_id))
    conn.commit()
    return cur.rowcount == 1

def renew_lease(conn, job_id, worker_id, *, lease_s, now=None) -> bool:
    n = now or _utc_now()
    lease = (n + timedelta(seconds=lease_s)).isoformat()
    cur = conn.execute(
        "UPDATE jobs SET lease_expires_at=? WHERE job_id=? AND claimed_by=? "
        "AND status='running'", (lease, job_id, worker_id))
    conn.commit()
    return cur.rowcount == 1

def reclaim_expired(conn, *, now=None) -> int:
    """Expired (or NULL-lease) running jobs -> pending, claim fields cleared.
    Lease expiry makes a job reclaimable; it never fails the job. Returns count."""
    n = (now or _utc_now()).isoformat()
    cur = conn.execute(
        "UPDATE jobs SET status='pending', claimed_by=NULL, claimed_at=NULL, "
        "lease_expires_at=NULL "
        "WHERE status='running' AND (lease_expires_at IS NULL OR lease_expires_at < ?)",
        (n,))
    conn.commit()
    return cur.rowcount
```
(`update_job(**fields)` already supports arbitrary columns, so the tests' `update_job(c,"j1",lease_expires_at=...)` work without change. No `reclaim_count` column, no fail branch.)

- [ ] **Step 4: Run, verify pass** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_worker_db.py -v` → all pass.

- [ ] **Step 5: Regression — existing db/queue tests** —
```bash
cd /root/all_project_models/alfaj/text-to-cad-product
/root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_db.py tests/test_v1_queue.py tests/test_v1_queue_terminal.py -q
```
Expected: all pass (additive columns don't disturb existing reads/writes).

- [ ] **Step 6: Engine-freeze guard + commit**
```bash
git diff --stat v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator   # empty
git add backend/app/v1/db.py tests/test_v1_worker_db.py
git commit -m "feat(v1): additive worker columns + atomic claim/lease/reclaim helpers (multi-worker prep)"
```

**Success:** 3 columns present (no `reclaim_count`) + idempotent; atomic single-winner claim; renew owner-only; reclaim expired→pending (never failed) clearing claim fields; repeated expiry never fails; unexpired untouched; NULL-lease running treated expired; existing tests green; guard empty.

---

## Task 2 — config knobs + `queue.py` claim-mode worker (gated)

**Files:**
- Modify: `backend/app/core/config.py`, `backend/app/v1/queue.py`
- Test: `tests/test_v1_worker_claim.py`

**Interfaces — Consumes:** `db.next_pending/count_pending/claim_job/renew_lease/reclaim_expired`, `config.API_WORKER_MODE/WORKER_ID/...`. **Produces:** `JobQueue` selects single vs claim path by `config.API_WORKER_MODE`; claim worker + heartbeat + lease recovery.

- [ ] **Step 1: Write the failing test**
```python
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
```

- [ ] **Step 2: Run, verify fail** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_worker_claim.py -v`
Expected: FAIL (claim mode not implemented).

- [ ] **Step 3: Implement config** — in `backend/app/core/config.py`, after the F10 block add:
```python
import socket, uuid   # ensure available at top of config.py
# --- /v1 multi-worker prep (P2) ---
API_WORKER_MODE = os.environ.get("API_WORKER_MODE", "single")   # "single" | "claim"
API_WORKER_LEASE_S = int(os.environ.get("API_WORKER_LEASE_S", "120"))
API_WORKER_HEARTBEAT_S = int(os.environ.get("API_WORKER_HEARTBEAT_S", "30"))
API_WORKER_POLL_S = float(os.environ.get("API_WORKER_POLL_S", "2"))
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
```

- [ ] **Step 4: Implement queue claim path** — in `backend/app/v1/queue.py`:

(a) `__init__` — capture mode + worker id (read at construction so tests monkeypatching cfg before `JobQueue(...)` take effect):
```python
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or config.API_DB_PATH
        self.mode = config.API_WORKER_MODE
        self.worker_id = config.WORKER_ID
        self._q: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._conn = None
```

(b) `start` — pick the worker coroutine by mode:
```python
    def start(self):
        if self._task is None:
            self._conn = db.connect(self.db_path)
            if self.mode == "claim":
                self._task = asyncio.create_task(self._claim_worker())
            else:
                self._task = asyncio.create_task(self._worker())
```

(c) `recover` — branch:
```python
    def recover(self):
        conn = db.connect(self.db_path)
        try:
            if self.mode == "claim":
                db.reclaim_expired(conn)
            else:
                for r in db.list_running_jobs(conn):
                    _terminal(conn, r["job_id"], r["project_id"], "failed",
                              failure_class="internal")
                for r in db.list_pending_jobs(conn):
                    self._q.put_nowait(r["job_id"])
        finally:
            conn.close()
```

(d) `depth` + `enqueue` — branch (single path verbatim):
```python
    def depth(self) -> int:
        if self.mode == "claim":
            c = db.connect(self.db_path)
            try: return db.count_pending(c)
            finally: c.close()
        return self._q.qsize()

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
```

(e) add the claim worker + heartbeat (leave `_worker` untouched):
```python
    async def _heartbeat(self, job_id):
        try:
            while True:
                await asyncio.sleep(config.API_WORKER_HEARTBEAT_S)
                db.renew_lease(self._conn, job_id, self.worker_id,
                               lease_s=config.API_WORKER_LEASE_S)
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
```
(`hb.cancel()` is explicit on every exit branch rather than a `finally`, to keep the existing terminal-write structure intact. The single-mode `_worker`, `_load_metrics`, `_terminal`, `_TERMINAL` are unchanged.)

- [ ] **Step 5: Run, verify pass** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_worker_claim.py -v` → all pass.

- [ ] **Step 6: Regression — single mode unchanged** —
```bash
cd /root/all_project_models/alfaj/text-to-cad-product
/root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_queue.py tests/test_v1_queue_terminal.py tests/test_v1_queue_pos.py -q
```
Expected: all pass (default `API_WORKER_MODE="single"` → original path).

- [ ] **Step 7: Engine-freeze guard + commit**
```bash
git diff --stat v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator   # empty
git add backend/app/core/config.py backend/app/v1/queue.py tests/test_v1_worker_claim.py
git commit -m "feat(v1): gated claim-mode worker + heartbeat + lease recovery (multi-worker prep)"
```

**Success:** claim mode claims+runs+completes a job (claimed_by set); claim-mode recover reclaims→pending (not fail-all); single-mode recover still fail-all; depth counts pending in claim mode; single mode byte-for-byte unchanged; guard empty.

---

## Task 3 — verification

**Files:** test only.
- [ ] **Step 1: /v1 suite (default single mode)** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_*.py -q` → all pass.
- [ ] **Step 2: full suite** — `/root/anaconda3/envs/cadskills/bin/python -m pytest tests/ -q` → all pass.
- [ ] **Step 3: engine-freeze guard** — `git diff --stat v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator` → empty.
- [ ] **Step 4: commit** (only if test-only fixups) `git commit -m "test(v1): multi-worker prep full verification"` (skip if nothing to commit).

**Success:** /v1 + full suites green in default single mode; guard empty.

---

## Self-review
**Spec coverage:** 3 additive columns idempotent, no `reclaim_count` (T1 `_add_column_if_missing`, schema test asserts absence) ✓; atomic single-winner claim (T1 `claim_job`, two-conn test) ✓; lease+heartbeat (T1 `renew_lease`, T2 `_heartbeat`) ✓; lease-scoped recovery, **expiry→reclaimable→pending, clears claim fields, NEVER failed** (T1 tests: pending+cleared, repeated-expiry-never-fails) ✓; NULL-lease running treated expired (T1 test) ✓; no cap / no poison-job handling (deferred to roadmap) ✓; flag gate `API_WORKER_MODE` single=current/claim=new (T2 start/recover/depth/enqueue branch; single-mode regression) ✓; poll-based queue source (T2 `_claim_worker`) ✓; worker_id (T2 config `WORKER_ID`) ✓; config knobs, no `MAX_RECLAIM` (T2) ✓; single default byte-for-byte (T2 step6 regression, T3) ✓; no engine/main.py/frontend/benchmark changes ✓; SQLite single-host ✓.
**Placeholder scan:** none — all code concrete.
**Type consistency:** `claim_job(conn,job_id,worker_id,*,lease_s,now=None)->bool`, `renew_lease(...)->bool`, `reclaim_expired(conn,*,now=None)->int`, `next_pending(conn)->Row|None`, `count_pending(conn)->int` consistent T1↔T2; `JobQueue.mode/worker_id`, `_claim_worker`, `_heartbeat` consistent; `_terminal`/`_TERMINAL`/`_worker` reused unchanged. `config.WORKER_ID`/`API_WORKER_*` names consistent.
