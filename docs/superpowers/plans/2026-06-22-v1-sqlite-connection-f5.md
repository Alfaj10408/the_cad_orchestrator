# F5 — Per-context SQLite Connections — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace the single shared SQLite connection with per-request + per-worker connections (+ `busy_timeout`), keeping `db.*` CRUD signatures and the schema unchanged.

**Architecture:** `get_conn()` dependency yields a fresh per-request connection (opened from `config.API_DB_PATH`, closed at teardown); `JobQueue` owns its own worker connection; `readyz` uses a short-lived connection; the shared `app.state.db` connection is removed.

**Tech Stack:** Python 3.11 (`/root/anaconda3/envs/cadskills/bin/python`), FastAPI, sqlite3, pytest.

## Global Constraints
- **No schema change. No `db.*` CRUD signature change** (still `fn(conn, …)`). No `/v1` contract change. No frontend/benchmark/CAD changes.
- Edits ONLY in `backend/app/v1/{db,auth,routes,queue}.py`, `backend/app/main.py`, `backend/app/core/config.py`, and `tests/test_v1_*`. **Never** `backend/app/services` or `backend/app/orchestrator` — STOP/BLOCKED if a task seems to require it.
- `get_conn` and `JobQueue` read `config.API_DB_PATH` directly (no `app.state.db`). Tests set `API_DB_PATH` (env) / monkeypatch `config.API_DB_PATH` instead of injecting `app.state.db`.
- Each commit must leave the app importable + the named suites green.
- cadskills python only; run from product root. Guard-check every commit: `git show --stat <sha> | grep -E 'services/|orchestrator/'` must be empty.

---

## Task 1 — config + `db.py` (busy_timeout + `get_conn`)

**Files:** Modify `backend/app/core/config.py`, `backend/app/v1/db.py`; Test `tests/test_v1_conn.py`.

**Interfaces — Produces:** `config.API_DB_BUSY_TIMEOUT_MS` (int, default 5000); `connect()` sets `busy_timeout`; `db.get_conn()` generator dependency (open→yield→rollback-on-error→close).

- [ ] **Step 1: failing test**
```python
# tests/test_v1_conn.py
import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
from app.v1 import db
from app.core import config as cfg

def test_connect_sets_busy_timeout(tmp_path):
    c = db.connect(str(tmp_path / "b.db"))
    bt = c.execute("PRAGMA busy_timeout").fetchone()[0]
    assert bt == cfg.API_DB_BUSY_TIMEOUT_MS == 5000
    c.close()

def test_get_conn_yields_and_closes(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "API_DB_PATH", str(tmp_path / "g.db"))
    gen = db.get_conn()
    conn = next(gen)
    db.init_db(conn)
    db.create_user(conn, "x")          # usable
    try: next(gen)
    except StopIteration: pass         # generator finalizes (closes)
    import pytest
    with pytest.raises(Exception):     # closed connection rejects use
        conn.execute("SELECT 1")
```

- [ ] **Step 2: run, verify fail** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_conn.py -v`

- [ ] **Step 3: implement**

`config.py` (after `API_DB_PATH`):
```python
API_DB_BUSY_TIMEOUT_MS = int(os.environ.get("API_DB_BUSY_TIMEOUT_MS", "5000"))
```
`db.py` — in `connect()`, after the WAL pragma add:
```python
    conn.execute(f"PRAGMA busy_timeout={config.API_DB_BUSY_TIMEOUT_MS}")
```
Add the dependency (after `connect`):
```python
def get_conn():
    """FastAPI dependency: a fresh per-request connection, closed at teardown."""
    conn = connect()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

- [ ] **Step 4: run, verify pass.**
- [ ] **Step 5: commit** `git add backend/app/core/config.py backend/app/v1/db.py tests/test_v1_conn.py && git commit -m "feat(v1): busy_timeout + get_conn per-request dependency (F5)"`

**Rollback:** revert; additive (get_conn unused until T2/T3).
**Success:** busy_timeout=5000; get_conn yields a usable conn and closes it.

---

## Task 2 — `auth.py` deps use `get_conn`

**Files:** Modify `backend/app/v1/auth.py`; Test `tests/test_v1_auth.py` (extend).

**Exact change:** `require_user` obtains the request connection via `Depends(db.get_conn)` instead of `request.app.state.db`. `require_admin` unchanged (no DB).

- [ ] **Step 1: failing test** (dep resolves user with a connection from config path)
```python
# append to tests/test_v1_auth.py
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

def test_require_user_dep_uses_get_conn(tmp_path, monkeypatch):
    monkeypatch.setattr(auth.config, "API_DB_PATH", str(tmp_path / "ru.db"))
    c = db.connect(str(tmp_path / "ru.db")); db.init_db(c)
    key, *_ , uid = auth.mint_key(c, "u"); c.close()
    app = FastAPI()
    @app.get("/who")
    def who(user_id: str = Depends(auth.require_user)):
        return {"user_id": user_id}
    tc = TestClient(app)
    assert tc.get("/who", headers={"Authorization": f"Bearer {key}"}).json()["user_id"] == uid
    assert tc.get("/who").status_code == 401
```

- [ ] **Step 2: run, verify fail.**

- [ ] **Step 3: implement** — replace `require_user`:
```python
def require_user(authorization: str | None = Header(default=None),
                 conn=Depends(db.get_conn)) -> str:
    return _resolve_user(conn, authorization)
```
(`require_admin` unchanged. Keep `from fastapi import Depends, Header, HTTPException, Request` import; `Request` may now be unused in this file — leave or drop, but do not break imports.)

- [ ] **Step 4: run, verify pass** (+ existing `tests/test_v1_auth.py` cases).
- [ ] **Step 5: commit** `git add backend/app/v1/auth.py tests/test_v1_auth.py && git commit -m "feat(v1): require_user uses per-request get_conn (F5)"`

**Rollback:** revert.
**Success:** `require_user` resolves via a dependency-provided connection; 401 path intact.

---

## Task 3 — `routes.py` per-request conn + short-lived `readyz`

**Files:** Modify `backend/app/v1/routes.py`; Test `tests/test_v1_routes_jobs.py`, `tests/test_v1_admin.py`, `tests/test_v1_health_events.py` (harness update).

**Exact change:** every route takes `conn = Depends(db.get_conn)` and uses it; remove `_conn(request)` + `app.state.db` usage; `_owned_row` takes `conn`. `readyz` opens a short-lived connection. Route/admin/health test harnesses set `config.API_DB_PATH` (monkeypatch) instead of `app.state.db`.

- [ ] **Step 1: update tests** — in each affected test, replace `app.state.db = conn` injection with `monkeypatch.setattr(<module>.config or app.core.config, "API_DB_PATH", str(tmp_path/"x.db"))` + `db.init_db(db.connect(path))` once; mint keys via a direct `db.connect(path)`; keep `app.state.queue = <fake>`. (Routes no longer read `app.state.db`.) Add the assertion that artifacts/jobs still behave identically.

- [ ] **Step 2: run, verify fail** (routes still reference `app.state.db`).

- [ ] **Step 3: implement** — edit `routes.py`:
- Remove `_conn(request)` helper. Change `_owned_row` to `_owned_row(conn, job_id, user_id)`.
- Each handler: add `conn=Depends(db.get_conn)`, replace `_conn(request)` → `conn`, `_owned_row(request, …)` → `_owned_row(conn, …)`. Example for `create_job`:
```python
@router.post("/jobs", status_code=201)
def create_job(body: JobCreate, request: Request, user_id: str = Depends(auth.require_user),
               conn=Depends(db.get_conn)):
    ...
    db.insert_job(conn, job.job_id, user_id, pid, status="pending")
    try:
        pos = request.app.state.queue.enqueue(job.job_id)
    except RuntimeError:
        db.update_job(conn, job.job_id, status="failed", failure_class="internal")
        raise HTTPException(status_code=429, detail="queue full")
    db.update_job(conn, job.job_id, queue_pos=pos)
    return {"job_id": job.job_id, "status": "pending", "queue_pos": pos}
```
(Apply the same `conn=Depends(db.get_conn)` + `_owned_row(conn,…)` pattern to `whoami`, `get_job`, `cancel_job`, `list_artifacts`, `download_artifact`, `admin_mint_key`, `admin_revoke_key`. `stream_events` resolves the row via `_owned_row(conn, …)` too.) `request.app.state.queue` access stays.
- `readyz`: replace `request.app.state.db.execute("SELECT 1")` with a short-lived connection:
```python
    try:
        c = db.connect(); c.execute("SELECT 1"); c.close(); checks["db"] = True
    except Exception:
        checks["db"] = False
```

- [ ] **Step 4: run, verify pass** — `pytest tests/test_v1_routes_jobs.py tests/test_v1_admin.py tests/test_v1_health_events.py -v`.
- [ ] **Step 5: commit** `git add backend/app/v1/routes.py tests/test_v1_routes_jobs.py tests/test_v1_admin.py tests/test_v1_health_events.py && git commit -m "feat(v1): routes use per-request get_conn; readyz short-lived conn (F5)"`

**Rollback:** revert.
**Success:** all job/admin/health endpoints work via per-request connections; ownership/401/403/404 unchanged; no `app.state.db` reads remain in routes.

---

## Task 4 — `queue.py` worker-owned connection

**Files:** Modify `backend/app/v1/queue.py`; Test `tests/test_v1_queue.py` (harness update).

**Exact change:** `JobQueue(db_path=None)` (defaults `config.API_DB_PATH`); `start()` opens `self._conn = db.connect(self.db_path)`; `_worker` uses `self._conn`; `recover()` (no arg) opens+closes a short-lived connection; `stop()` closes `self._conn`.

- [ ] **Step 1: update test** — `tests/test_v1_queue.py`: construct `JobQueue(str(tmp_path/"q.db"))` (path, not conn); init the db at that path; for the recover test call `jq.recover()` (no arg). Keep the run→completed and cancelled-guard and recover→failed assertions.

- [ ] **Step 2: run, verify fail.**

- [ ] **Step 3: implement** — edit `JobQueue`:
```python
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or config.API_DB_PATH
        self._q: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._conn = None

    def start(self):
        if self._task is None:
            self._conn = db.connect(self.db_path)
            self._task = asyncio.create_task(self._worker())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try: await self._task
            except asyncio.CancelledError: pass
            self._task = None
        if self._conn is not None:
            self._conn.close(); self._conn = None

    def recover(self):
        conn = db.connect(self.db_path)
        try:
            for r in db.list_running_jobs(conn):
                db.update_job(conn, r["job_id"], status="failed",
                              failure_class="internal", completed_at=_now())
            for r in db.list_pending_jobs(conn):
                self._q.put_nowait(r["job_id"])
        finally:
            conn.close()
```
In `_worker`, replace every `self.conn` with `self._conn`. (`enqueue`, `depth`, `alive` unchanged.)

- [ ] **Step 4: run, verify pass** — `pytest tests/test_v1_queue.py -v`.
- [ ] **Step 5: commit** `git add backend/app/v1/queue.py tests/test_v1_queue.py && git commit -m "feat(v1): JobQueue owns its worker connection; recover() short-lived (F5)"`

**Rollback:** revert.
**Success:** worker uses its own connection; recover()/stop() manage their own; tests green.

---

## Task 5 — `main.py` lifespan (path-based, drop shared conn)

**Files:** Modify `backend/app/main.py`; Test `tests/test_v1_integration.py` (already sets `API_DB_PATH`).

**Exact change:** lifespan inits the schema via a short-lived connection, builds `JobQueue()` (reads config path), `recover()`+`start()`; removes the shared `app.state.db` connection. Everything else in main.py unchanged.

- [ ] **Step 1: confirm/extend test** — `tests/test_v1_integration.py` already sets `API_DB_PATH` + drives admin-mint→create→completed. Confirm it still asserts COMPLETED; it should pass once main no longer holds a shared conn. (No change likely needed; if it referenced `app.state.db`, drop that.)

- [ ] **Step 2: run, verify fail** (main still builds `JobQueue(conn)` / sets `app.state.db`).

- [ ] **Step 3: implement** — replace the lifespan body:
```python
@asynccontextmanager
async def _lifespan(app):
    c = v1db.connect(); v1db.init_db(c); c.close()
    q = JobQueue()                      # reads config.API_DB_PATH
    q.recover(); q.start()
    app.state.queue = q
    try:
        yield
    finally:
        await q.stop()
        await claude_code_adapter.shutdown()
```
(Remove `app.state.db = conn`. Keep CORS middleware, all `/api` + `/v1` router registration, and the StaticFiles mount exactly as-is.)

- [ ] **Step 4: run, verify pass** — `pytest tests/test_v1_integration.py -v`.
- [ ] **Step 5: commit** `git add backend/app/main.py tests/test_v1_integration.py && git commit -m "feat(v1): lifespan path-based init + worker-owned conn; drop shared app.state.db (F5)"`

**Rollback:** revert.
**Success:** end-to-end /v1 works with no shared connection; integration green.

---

## Task 6 — concurrency test + full verification

**Files:** Test `tests/test_v1_concurrency.py` (new). No product code.

- [ ] **Step 1: concurrent-request test** — build the app (set `API_DB_PATH` tmp, init db, mint a key, inject a fake queue whose `enqueue` is a no-op), fire N=20 parallel `POST /v1/jobs` + `GET /v1/jobs/{id}` from a `ThreadPoolExecutor` via `TestClient`; assert no 500s / no `database is locked`, every create returns 201, rows readable. Run: `pytest tests/test_v1_concurrency.py -v`.
- [ ] **Step 2: /v1 suite** — `pytest tests/test_v1_*.py -q` all pass.
- [ ] **Step 3: full suite** — `pytest tests/ -q` all pass (existing CAD/benchmark/orchestrator unchanged).
- [ ] **Step 4: engine-freeze guard** — `git diff v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator` → empty.
- [ ] **Step 5: commit** test `git add tests/test_v1_concurrency.py && git commit -m "test(v1): concurrent-request DB safety (F5 verification)"`

**Success:** concurrent test green; /v1 + full suites green; guard empty.

---

## Self-review
**Spec coverage:** busy_timeout + get_conn (T1) ✓; per-request conn in auth+routes (T2,T3) ✓; worker-owned conn + short-lived recover (T4) ✓; lifespan drops shared conn (T5) ✓; readyz short-lived (T3) ✓; concurrency + full verification + guard (T6) ✓; db CRUD signatures + schema unchanged (all tasks keep `db.fn(conn,…)`) ✓; no services/orchestrator/frontend/benchmark (Global Constraints) ✓.
**Placeholder scan:** none.
**Type consistency:** `get_conn()` generator; `require_user(authorization, conn=Depends(get_conn))->str`; routes `conn=Depends(db.get_conn)`; `_owned_row(conn, job_id, user_id)`; `JobQueue(db_path=None)` with `recover()`/`start()`/`stop()`. Consistent across tasks.
