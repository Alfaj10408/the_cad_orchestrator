# Production API `/v1` (Phase 1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add an authenticated, durable `/v1` facade (SQLite + in-process queue + Bearer auth + admin key + healthz/readyz) that wraps the existing pipeline, preserving `v0.1-benchmark-10of10` behavior.

**Architecture:** New `backend/app/v1/` package (db, auth, queue, models, routes). One uvicorn process; `/api/*` unchanged; `/v1/*` calls existing services (`claude_generation`, `job_service`, `event_service`, `artifact_service`, `claude_code_adapter`). SQLite `storage/api.db` is the queryable index; per-job JSON files remain source of truth.

**Tech Stack:** Python 3.11 (`/root/anaconda3/envs/cadskills/bin/python`), FastAPI, pytest, sqlite3 (stdlib).

## Global Constraints
- **No changes** to: CAD generation logic, prompts, component/assembly orchestration, config generation defaults, frontend, benchmark code, or any `/api/*` route. `/v1` only *calls* existing services.
- New code only under `backend/app/v1/` + additive lines in `backend/app/core/config.py` and `backend/app/main.py` (router registration + lifespan worker) + `scripts/serve_api.sh`.
- Tests at product-root `tests/`; run from product root `/root/all_project_models/alfaj/text-to-cad-product` with `/root/anaconda3/envs/cadskills/bin/python`. Do NOT use uv/3.13.
- All `/v1` tests mock the Claude pipeline (`claude_generation.run`) — no real CAD/Claude in unit tests.
- SQLite access via a `connect(path)` that honors `config.API_DB_PATH`; tests pass a tmp path.
- Git: product-root repo; commit each task.

---

## Task 1 — config keys + `v1/db.py` (SQLite schema + CRUD)

**Files:** Modify `backend/app/core/config.py`; Create `backend/app/v1/__init__.py`, `backend/app/v1/db.py`; Test `tests/test_v1_db.py`.

**Interfaces — Produces:**
- config: `API_DB_PATH`, `ADMIN_API_KEY`, `API_KEY_SALT`, `API_MAX_QUEUE_DEPTH`, `JOB_WALLCLOCK_TIMEOUT`, `V1_CORS_ORIGINS`.
- `db.connect(path=None) -> sqlite3.Connection` (WAL, row factory); `db.init_db(conn)`; CRUD: `create_user`, `get_user`, `create_api_key`, `get_key_by_hash`, `revoke_key`, `insert_job`, `update_job`, `get_job_row`, `list_pending_jobs`, `list_running_jobs`.

- [ ] **Step 1: failing test**
```python
# tests/test_v1_db.py
import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
from app.v1 import db

def _conn(tmp_path):
    c = db.connect(str(tmp_path / "t.db")); db.init_db(c); return c

def test_user_key_job_roundtrip(tmp_path):
    c = _conn(tmp_path)
    uid = db.create_user(c, "alice", is_admin=False)
    assert db.get_user(c, uid)["name"] == "alice"
    kid = db.create_api_key(c, uid, key_hash="h1", key_prefix="sk_abc")
    assert db.get_key_by_hash(c, "h1")["user_id"] == uid
    db.insert_job(c, "j1", uid, "p1", status="pending")
    db.update_job(c, "j1", status="running", stage="COMPONENT_GENERATION")
    row = db.get_job_row(c, "j1")
    assert row["status"] == "running" and row["user_id"] == uid and row["project_id"] == "p1"
    assert [r["job_id"] for r in db.list_pending_jobs(c)] == []   # none pending now
    db.revoke_key(c, kid)
    assert db.get_key_by_hash(c, "h1") is None                    # revoked keys not returned
```

- [ ] **Step 2: run, verify fail** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_db.py -v` → FAIL.

- [ ] **Step 3: implement**

`backend/app/v1/__init__.py`: empty.

Add to `config.py`:
```python
# ---------- /v1 production API ----------
API_DB_PATH = os.environ.get("API_DB_PATH", str(STORAGE_ROOT / "api.db"))
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")
API_KEY_SALT = os.environ.get("API_KEY_SALT", "dev-salt-change-me")
API_MAX_QUEUE_DEPTH = int(os.environ.get("API_MAX_QUEUE_DEPTH", "32"))
JOB_WALLCLOCK_TIMEOUT = int(os.environ.get("JOB_WALLCLOCK_TIMEOUT", "5400"))
V1_CORS_ORIGINS = [o for o in os.environ.get("V1_CORS_ORIGINS", "").split(",") if o]
```

`backend/app/v1/db.py`:
```python
"""SQLite index for the /v1 API (users, api_keys, jobs). WAL; stdlib sqlite3."""
from __future__ import annotations
import sqlite3, uuid
from datetime import datetime, timezone
from app.core import config

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or config.API_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      id TEXT PRIMARY KEY, name TEXT, is_admin INTEGER DEFAULT 0, created_at TEXT);
    CREATE TABLE IF NOT EXISTS api_keys(
      id TEXT PRIMARY KEY, user_id TEXT, key_hash TEXT UNIQUE, key_prefix TEXT,
      created_at TEXT, revoked_at TEXT);
    CREATE TABLE IF NOT EXISTS jobs(
      job_id TEXT PRIMARY KEY, user_id TEXT, project_id TEXT, status TEXT, stage TEXT,
      failure_class TEXT, created_at TEXT, started_at TEXT, completed_at TEXT,
      queue_pos INTEGER, metrics_json TEXT);
    """)
    conn.commit()

def create_user(conn, name, is_admin=False) -> str:
    uid = uuid.uuid4().hex
    conn.execute("INSERT INTO users(id,name,is_admin,created_at) VALUES(?,?,?,?)",
                 (uid, name, 1 if is_admin else 0, _now())); conn.commit()
    return uid

def create_api_key(conn, user_id, key_hash, key_prefix) -> str:
    kid = uuid.uuid4().hex
    conn.execute("INSERT INTO api_keys(id,user_id,key_hash,key_prefix,created_at) VALUES(?,?,?,?,?)",
                 (kid, user_id, key_hash, key_prefix, _now())); conn.commit()
    return kid

def get_user(conn, user_id):
    return conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()

def get_key_by_hash(conn, key_hash):
    return conn.execute(
        "SELECT * FROM api_keys WHERE key_hash=? AND revoked_at IS NULL", (key_hash,)).fetchone()

def revoke_key(conn, key_id) -> None:
    conn.execute("UPDATE api_keys SET revoked_at=? WHERE id=?", (_now(), key_id)); conn.commit()

def insert_job(conn, job_id, user_id, project_id, status="pending", queue_pos=None) -> None:
    conn.execute("INSERT INTO jobs(job_id,user_id,project_id,status,created_at,queue_pos) "
                 "VALUES(?,?,?,?,?,?)", (job_id, user_id, project_id, status, _now(), queue_pos))
    conn.commit()

def update_job(conn, job_id, **fields) -> None:
    if not fields: return
    cols = ",".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE jobs SET {cols} WHERE job_id=?", (*fields.values(), job_id)); conn.commit()

def get_job_row(conn, job_id):
    return conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()

def list_pending_jobs(conn):
    return conn.execute("SELECT * FROM jobs WHERE status='pending' ORDER BY created_at").fetchall()

def list_running_jobs(conn):
    return conn.execute("SELECT * FROM jobs WHERE status='running'").fetchall()
```

- [ ] **Step 4: run, verify pass.**
- [ ] **Step 5: commit** `git add backend/app/core/config.py backend/app/v1/ tests/test_v1_db.py && git commit -m "feat(v1): SQLite index (users/api_keys/jobs) + config keys"`

**Tests/verification:** unit above.
**Rollback:** revert commit; new package + additive config, no callers yet.
**Success criteria:** roundtrip CRUD; revoked keys excluded; pending list ordered.

---

## Task 2 — `v1/auth.py` (key hashing + FastAPI deps)

**Files:** Create `backend/app/v1/auth.py`; Test `tests/test_v1_auth.py`.

**Interfaces — Produces:** `hash_key(key)->str`; `gen_key()->str`; `require_user` / `require_admin` (FastAPI deps returning `user_id` / admin sentinel); helper `mint_key(conn, user_name)->(key_plaintext, key_prefix, key_id, user_id)`.

- [ ] **Step 1: failing test** (pure parts + dep logic via a fake request)
```python
# tests/test_v1_auth.py
import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
import pytest
from fastapi import HTTPException
from app.v1 import db, auth

def _conn(tmp_path):
    c = db.connect(str(tmp_path/"a.db")); db.init_db(c); return c

def test_hash_deterministic_and_prefix():
    k = auth.gen_key()
    assert k.startswith("sk_")
    assert auth.hash_key(k) == auth.hash_key(k)
    assert auth.hash_key(k) != auth.hash_key(auth.gen_key())

def test_resolve_user_from_key(tmp_path):
    c = _conn(tmp_path)
    key, prefix, kid, uid = auth.mint_key(c, "bob")
    assert auth._resolve_user(c, f"Bearer {key}") == uid
    with pytest.raises(HTTPException):
        auth._resolve_user(c, "Bearer sk_wrong")
    with pytest.raises(HTTPException):
        auth._resolve_user(c, None)

def test_admin_check(monkeypatch):
    monkeypatch.setattr(auth.config, "ADMIN_API_KEY", "admin-secret")
    assert auth._is_admin("Bearer admin-secret") is True
    assert auth._is_admin("Bearer nope") is False
    monkeypatch.setattr(auth.config, "ADMIN_API_KEY", "")
    assert auth._is_admin("Bearer admin-secret") is False   # empty admin key disables admin
```

- [ ] **Step 2: run, verify fail.**

- [ ] **Step 3: implement**
```python
# backend/app/v1/auth.py
"""Bearer API-key auth for /v1."""
from __future__ import annotations
import hashlib, hmac, secrets
from fastapi import Depends, Header, HTTPException, Request
from app.core import config
from app.v1 import db

def gen_key() -> str:
    return "sk_" + secrets.token_urlsafe(32)

def hash_key(key: str) -> str:
    return hashlib.sha256((config.API_KEY_SALT + key).encode()).hexdigest()

def mint_key(conn, user_name: str, is_admin: bool = False):
    uid = db.create_user(conn, user_name, is_admin=is_admin)
    key = gen_key(); prefix = key[:10]
    kid = db.create_api_key(conn, uid, key_hash=hash_key(key), key_prefix=prefix)
    return key, prefix, kid, uid

def _bearer(value: str | None) -> str | None:
    if not value or not value.startswith("Bearer "):
        return None
    return value[len("Bearer "):].strip()

def _resolve_user(conn, authorization: str | None) -> str:
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    row = db.get_key_by_hash(conn, hash_key(token))
    if row is None:
        raise HTTPException(status_code=401, detail="invalid api key")
    return row["user_id"]

def _is_admin(authorization: str | None) -> bool:
    token = _bearer(authorization)
    admin = config.ADMIN_API_KEY
    return bool(admin) and token is not None and hmac.compare_digest(token, admin)

# FastAPI deps (conn provided by app state via dependency in routes module)
def require_user(request: Request, authorization: str | None = Header(default=None)) -> str:
    return _resolve_user(request.app.state.db, authorization)

def require_admin(authorization: str | None = Header(default=None)) -> bool:
    if not _is_admin(authorization):
        raise HTTPException(status_code=403, detail="admin only")
    return True
```

- [ ] **Step 4: run, verify pass.**
- [ ] **Step 5: commit** `git add backend/app/v1/auth.py tests/test_v1_auth.py && git commit -m "feat(v1): bearer api-key auth (hash, mint, user/admin deps)"`

**Rollback:** revert; no callers yet.
**Success criteria:** deterministic salted hash; key resolution + admin compare correct; empty `ADMIN_API_KEY` disables admin.

---

## Task 3 — `v1/queue.py` (in-process queue + worker + recovery)

**Files:** Create `backend/app/v1/queue.py`; Test `tests/test_v1_queue.py`.

**Interfaces — Consumes:** `db`, `claude_generation.run`, `job_service`. **Produces:** class `JobQueue` with `start()`, `stop()`, `enqueue(job_id) -> int (pos)`, `depth()`, `recover(conn)`; worker runs `await claude_generation.run(project_id, job_id)` then persists terminal status.

- [ ] **Step 1: failing test** (mock `claude_generation.run`; drive one job through)
```python
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
```

- [ ] **Step 2: run, verify fail.**

- [ ] **Step 3: implement**
```python
# backend/app/v1/queue.py
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
```

- [ ] **Step 4: run, verify pass.**
- [ ] **Step 5: commit** `git add backend/app/v1/queue.py tests/test_v1_queue.py && git commit -m "feat(v1): in-process job queue + worker + restart recovery"`

**Rollback:** revert; not wired until Task 5/7.
**Success criteria:** worker runs the (mocked) pipeline, persists terminal status + metrics; recovery fails orphaned running jobs and re-enqueues pending.

---

## Task 4 — `v1/models.py` + `v1/routes.py` job endpoints

**Files:** Create `backend/app/v1/models.py`, `backend/app/v1/routes.py`; Test `tests/test_v1_routes_jobs.py`.

**Interfaces — Consumes:** db, auth, queue, `paths`, `job_service`, `artifact_service`. **Produces:** `router` (APIRouter prefix `/v1`) with `POST /jobs`, `GET /jobs/{id}`, `POST /jobs/{id}/cancel`, `GET /jobs/{id}/artifacts`, `GET /jobs/{id}/artifacts/{name}`. App holds `app.state.db` + `app.state.queue`.

- [ ] **Step 1: failing test** (TestClient; mock pipeline + queue so create returns synchronously)
```python
# tests/test_v1_routes_jobs.py
import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.v1 import db, auth, routes

def _app(tmp_path):
    app = FastAPI()
    conn = db.connect(str(tmp_path/"r.db")); db.init_db(conn)
    class _Q:
        def __init__(s): s.enqueued=[]
        def enqueue(s, jid): s.enqueued.append(jid); return len(s.enqueued)
        def depth(s): return 0
    app.state.db = conn; app.state.queue = _Q()
    app.include_router(routes.router)
    return app, conn

def test_create_and_get_job_requires_auth(tmp_path):
    app, conn = _app(tmp_path)
    c = TestClient(app)
    assert c.post("/v1/jobs", json={"prompt":"a block"}).status_code == 401
    key,_,_,uid = auth.mint_key(conn, "u")
    h = {"Authorization": f"Bearer {key}"}
    r = c.post("/v1/jobs", json={"prompt":"a 40x30x10 block"}, headers=h)
    assert r.status_code == 201
    jid = r.json()["job_id"]
    assert app.state.queue.enqueued == [jid]
    s = c.get(f"/v1/jobs/{jid}", headers=h)
    assert s.status_code == 200 and s.json()["status"] == "pending"
    # ownership: another user cannot see it
    key2,_,_,_ = auth.mint_key(conn, "u2")
    assert c.get(f"/v1/jobs/{jid}", headers={"Authorization":f"Bearer {key2}"}).status_code == 404
    # /v1/me returns the authenticated user
    me = c.get("/v1/me", headers=h)
    assert me.status_code == 200 and me.json()["user_id"] == uid and me.json()["name"] == "u"
    assert c.get("/v1/me").status_code == 401  # unauth
```

- [ ] **Step 2: run, verify fail.**

- [ ] **Step 3: implement**

`backend/app/v1/models.py`:
```python
from __future__ import annotations
from pydantic import BaseModel, Field

class JobCreate(BaseModel):
    prompt: str = Field(min_length=1)
    dimensions: str | None = None
    material: str = "PLA"
    mode: str = "qwen_claude_code"

class JobView(BaseModel):
    job_id: str
    status: str
    stage: str | None = None
    failure_class: str | None = None
    queue_pos: int | None = None
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
```

`backend/app/v1/routes.py`:
```python
"""/v1 production API facade. Wraps existing pipeline services."""
from __future__ import annotations
import json, uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from app.core import config, paths
from app.services import job_service, artifact_service, claude_code_adapter, event_service
from app.v1 import auth, db
from app.v1.models import JobCreate, JobView

router = APIRouter(prefix="/v1", tags=["v1"])
_ALLOWED_MODES = {"qwen_claude_code", "deterministic"}

def _conn(request: Request):
    return request.app.state.db

def _owned_row(request, job_id, user_id):
    row = db.get_job_row(_conn(request), job_id)
    if row is None or row["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="job not found")
    return row

@router.post("/jobs", status_code=201)
def create_job(body: JobCreate, request: Request, user_id: str = Depends(auth.require_user)):
    if body.mode not in _ALLOWED_MODES:
        raise HTTPException(status_code=422, detail="invalid mode")
    if len(body.prompt) > config.CLAUDE_CODE_MAX_PROMPT_CHARS:
        raise HTTPException(status_code=422, detail="prompt too long")
    pid = uuid.uuid4().hex
    paths.ensure_project_skeleton(pid)
    (paths.project_dir(pid) / "brief.json").write_text(json.dumps({
        "project_id": pid, "prompt": body.prompt, "intent": "concept_cad",
        "parameters": {"dimensions": body.dimensions or "", "units": "mm",
                       "material": body.material},
        "user_answers": {"dimensions": body.dimensions or ""},
        "ready_to_generate": True, "generation_mode": body.mode}))
    job = job_service.create_job_full(pid, "generation", "CREATED")
    db.insert_job(_conn(request), job.job_id, user_id, pid, status="pending")
    try:
        pos = request.app.state.queue.enqueue(job.job_id)
    except RuntimeError:
        db.update_job(_conn(request), job.job_id, status="failed", failure_class="internal")
        raise HTTPException(status_code=429, detail="queue full")
    db.update_job(_conn(request), job.job_id, queue_pos=pos)
    return {"job_id": job.job_id, "status": "pending", "queue_pos": pos}

@router.get("/me")
def whoami(request: Request, user_id: str = Depends(auth.require_user)):
    u = db.get_user(_conn(request), user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="user not found")
    return {"user_id": user_id, "name": u["name"], "is_admin": bool(u["is_admin"])}

@router.get("/jobs/{job_id}", response_model=JobView)
def get_job(job_id: str, request: Request, user_id: str = Depends(auth.require_user)):
    r = _owned_row(request, job_id, user_id)
    return JobView(job_id=r["job_id"], status=r["status"], stage=r["stage"],
                   failure_class=r["failure_class"], queue_pos=r["queue_pos"],
                   created_at=r["created_at"], started_at=r["started_at"],
                   completed_at=r["completed_at"])

@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request, user_id: str = Depends(auth.require_user)):
    r = _owned_row(request, job_id, user_id)
    killed = claude_code_adapter.cancel(job_id)
    if r["status"] in ("pending", "running"):
        db.update_job(_conn(request), job_id, status="cancelled")
    return {"job_id": job_id, "requested": True, "killed_process": killed}

@router.get("/jobs/{job_id}/artifacts")
def list_artifacts(job_id: str, request: Request, user_id: str = Depends(auth.require_user)):
    r = _owned_row(request, job_id, user_id)
    listing = artifact_service.list_artifacts(r["project_id"])
    arts = listing.artifacts if listing else []
    return {"artifacts": [{"name": a.name, "category": a.category,
                           "relative_path": a.relative_path} for a in arts]}

@router.get("/jobs/{job_id}/artifacts/{name}")
def download_artifact(job_id: str, name: str, request: Request,
                      user_id: str = Depends(auth.require_user)):
    r = _owned_row(request, job_id, user_id)
    root = paths.project_dir(r["project_id"]).resolve()
    target = (root / "cad" / name).resolve()
    if not str(target).startswith(str(root)) or not target.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(str(target), filename=name)
```

- [ ] **Step 4: run, verify pass.**
- [ ] **Step 5: commit** `git add backend/app/v1/models.py backend/app/v1/routes.py tests/test_v1_routes_jobs.py && git commit -m "feat(v1): job endpoints (create/status/cancel/artifacts) with ownership"`

**Rollback:** revert; router not registered until Task 7.
**Success criteria:** auth enforced (401), job created+enqueued+persisted, status returns, cross-user access → 404, artifact path-safe.

---

## Task 5 — `/v1` events SSE + healthz/readyz

**Files:** Modify `backend/app/v1/routes.py`; Test `tests/test_v1_health_events.py`.

**Interfaces — Produces:** `GET /v1/jobs/{id}/events` (owner-scoped; reuses `app.api.events._gen`), `GET /v1/healthz`, `GET /v1/readyz`.

- [ ] **Step 1: failing test**
```python
# tests/test_v1_health_events.py
import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.v1 import db, auth, routes

def test_healthz_unauth(tmp_path):
    app = FastAPI(); conn = db.connect(str(tmp_path/"h.db")); db.init_db(conn)
    app.state.db = conn
    class _Q:  # readyz checks worker liveness
        def alive(s): return True
        def depth(s): return 0
    app.state.queue = _Q()
    app.include_router(routes.router)
    c = TestClient(app)
    assert c.get("/v1/healthz").json()["ok"] is True
    r = c.get("/v1/readyz").json()
    assert "checks" in r and "ready" in r
```

- [ ] **Step 2: run, verify fail.**

- [ ] **Step 3: implement** — append to `routes.py`:
```python
from fastapi import Header, Request as _Req
from fastapi.responses import StreamingResponse
from app.api.events import _gen  # reuse the validated SSE generator

@router.get("/jobs/{job_id}/events")
async def stream_events(job_id: str, request: Request,
                        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
                        user_id: str = Depends(auth.require_user)) -> StreamingResponse:
    r = _owned_row(request, job_id, user_id)
    try: last_id = int(last_event_id) if last_event_id else 0
    except ValueError: last_id = 0
    return StreamingResponse(_gen(r["project_id"], job_id, last_id, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"})

@router.get("/healthz")
def healthz():
    return {"ok": True}

@router.get("/readyz")
def readyz(request: Request):
    checks = {}
    try:
        request.app.state.db.execute("SELECT 1")
        checks["db"] = True
    except Exception:
        checks["db"] = False
    try:
        checks["claude_code"] = bool(claude_code_adapter.health().get("authenticated"))
    except Exception:
        checks["claude_code"] = False
    q = request.app.state.queue
    checks["worker"] = bool(getattr(q, "alive", lambda: True)())
    ready = all(checks.values())
    return {"ready": ready, "checks": checks}
```
Add an `alive()` method to `JobQueue` (Task 3 file): `def alive(self): return self._task is not None and not self._task.done()`.

- [ ] **Step 4: run, verify pass.**
- [ ] **Step 5: commit** `git add backend/app/v1/routes.py backend/app/v1/queue.py tests/test_v1_health_events.py && git commit -m "feat(v1): SSE events (reuse validated generator) + healthz/readyz"`

**Rollback:** revert.
**Success criteria:** healthz unauth ok; readyz reports checks; events owner-scoped reusing existing SSE.

---

## Task 6 — admin endpoints (mint/revoke keys)

**Files:** Modify `backend/app/v1/routes.py`; Test `tests/test_v1_admin.py`.

**Interfaces — Produces:** `POST /v1/admin/keys` (admin) → `{key, key_prefix, key_id, user_id}` (plaintext key once); `DELETE /v1/admin/keys/{key_id}` (admin).

- [ ] **Step 1: failing test**
```python
# tests/test_v1_admin.py
import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.v1 import db, auth, routes

def test_admin_mint_key(tmp_path, monkeypatch):
    monkeypatch.setattr(auth.config, "ADMIN_API_KEY", "admin-secret")
    app = FastAPI(); conn = db.connect(str(tmp_path/"ad.db")); db.init_db(conn)
    app.state.db = conn; app.state.queue = type("Q",(),{"enqueue":lambda s,j:1,"depth":lambda s:0})()
    app.include_router(routes.router)
    c = TestClient(app)
    assert c.post("/v1/admin/keys", json={"user_name":"x"}).status_code == 403  # no admin
    r = c.post("/v1/admin/keys", json={"user_name":"x"},
               headers={"Authorization":"Bearer admin-secret"})
    assert r.status_code == 201 and r.json()["key"].startswith("sk_")
    # minted key works as a user
    key = r.json()["key"]
    assert c.post("/v1/jobs", json={"prompt":"a block"},
                  headers={"Authorization":f"Bearer {key}"}).status_code == 201
```

- [ ] **Step 2: run, verify fail.**

- [ ] **Step 3: implement** — append to `routes.py`:
```python
from pydantic import BaseModel
class _KeyReq(BaseModel):
    user_name: str

@router.post("/admin/keys", status_code=201)
def admin_mint_key(body: _KeyReq, request: Request, _: bool = Depends(auth.require_admin)):
    key, prefix, kid, uid = auth.mint_key(_conn(request), body.user_name)
    return {"key": key, "key_prefix": prefix, "key_id": kid, "user_id": uid}

@router.delete("/admin/keys/{key_id}")
def admin_revoke_key(key_id: str, request: Request, _: bool = Depends(auth.require_admin)):
    db.revoke_key(_conn(request), key_id)
    return {"revoked": key_id}
```

- [ ] **Step 4: run, verify pass.**
- [ ] **Step 5: commit** `git add backend/app/v1/routes.py tests/test_v1_admin.py && git commit -m "feat(v1): admin endpoints to mint/revoke api keys"`

**Rollback:** revert.
**Success criteria:** admin-gated; minted key works for `POST /v1/jobs`.

---

## Task 7 — wire into `main.py` (lifespan worker + router) + `serve_api.sh` + integration

**Files:** Modify `backend/app/main.py`; Create `scripts/serve_api.sh`; Test `tests/test_v1_integration.py`.

**Exact behavior change:** Register `/v1` router; in a FastAPI lifespan, open `db.connect()` + `db.init_db`, set `app.state.db`, create `JobQueue`, `recover()` then `start()`, set `app.state.queue`; on shutdown `await queue.stop()` + `claude_code_adapter.shutdown()`. `/api/*` registration unchanged.

- [ ] **Step 1: failing test** (full app, pipeline mocked → job reaches completed)
```python
# tests/test_v1_integration.py
import sys, time
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))

def test_v1_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("API_DB_PATH", str(tmp_path/"api.db"))
    monkeypatch.setenv("ADMIN_API_KEY", "admin-secret")
    import importlib
    from app.core import config as cfg; importlib.reload(cfg)
    from app.services import claude_generation, job_service
    async def fake_run(project_id, job_id):
        j = job_service.get_job(job_id); j.status="COMPLETED"; j.stage="COMPLETED"; job_service.save_job(j)
    monkeypatch.setattr(claude_generation, "run", fake_run)
    from app import main as m; importlib.reload(m)
    from fastapi.testclient import TestClient
    with TestClient(m.app) as c:                       # triggers lifespan (worker starts)
        assert c.get("/v1/healthz").status_code == 200
        k = c.post("/v1/admin/keys", json={"user_name":"u"},
                   headers={"Authorization":"Bearer admin-secret"}).json()["key"]
        h = {"Authorization": f"Bearer {k}"}
        jid = c.post("/v1/jobs", json={"prompt":"a 40x30x10 block"}, headers=h).json()["job_id"]
        ok=False
        for _ in range(100):
            if c.get(f"/v1/jobs/{jid}", headers=h).json()["status"]=="completed": ok=True; break
            time.sleep(0.05)
        assert ok
```

- [ ] **Step 2: run, verify fail.**

- [ ] **Step 3: implement** — in `main.py`, add a lifespan and register the router (keep all existing `/api` registration). Use the existing imports + add:
```python
from contextlib import asynccontextmanager
from app.v1 import db as v1db, routes as v1routes
from app.v1.queue import JobQueue
from app.services import claude_code_adapter

@asynccontextmanager
async def _lifespan(app):
    conn = v1db.connect(); v1db.init_db(conn)
    app.state.db = conn
    q = JobQueue(conn); q.recover(conn); q.start()
    app.state.queue = q
    try:
        yield
    finally:
        await q.stop()
        await claude_code_adapter.shutdown()

app = FastAPI(title=APP_TITLE, version=APP_VERSION, lifespan=_lifespan)
# ... existing include_router(...) lines unchanged ...
app.include_router(v1routes.router)   # /v1 prefix is in the router itself
```
(If `app` is already constructed without `lifespan`, change only that constructor line to pass `lifespan=_lifespan`; leave every existing router registration intact.)

`scripts/serve_api.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${ADMIN_API_KEY:?set ADMIN_API_KEY}"
export GENERATION_PROVIDER="${GENERATION_PROVIDER:-qwen_claude_code}"
export CLAUDE_CODE_ENABLED="${CLAUDE_CODE_ENABLED:-1}"
export ORCH_BASE_URL="${ORCH_BASE_URL:-http://127.0.0.1:8001/v1}"
exec env LD_PRELOAD=/root/anaconda3/envs/cadskills/lib/libexpat.so.1 \
  /root/anaconda3/envs/cadskills/bin/uvicorn app.main:app \
  --app-dir backend --host 0.0.0.0 --port "${PORT:-8080}"
```

- [ ] **Step 4: run, verify pass.**

- [ ] **Step 5: regression + commit**

Run the FULL existing suite to prove no `/api`/pipeline regression:
`cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/ -q`
```bash
git add backend/app/main.py scripts/serve_api.sh tests/test_v1_integration.py && chmod +x scripts/serve_api.sh
git commit -m "feat(v1): register /v1 router + lifespan queue worker + serve_api.sh"
```

**Tests/verification:** integration above + full suite green.
**Rollback strategy:** revert this commit → `/v1` unmounted, `/api` fully intact (the only `main.py` change is the lifespan + one include_router).
**Success criteria:** lifespan starts worker; end-to-end `/v1` (admin mint → create → poll → completed) works with mocked pipeline; all existing tests pass.

---

## Task 8 — verification (no code changes)
- [ ] Full unit suite green (`pytest tests/ -q`), incl. all existing CAD/benchmark tests unchanged.
- [ ] Manual smoke (optional, real): `ADMIN_API_KEY=… scripts/serve_api.sh`; `curl /v1/healthz`, `/v1/readyz`; admin-mint a key; `POST /v1/jobs` for a calibration block; poll status to `completed`; download `model.step`. Confirm `/api/*` still serves the frontend unchanged.
- [ ] Confirm `git diff v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator` shows **no** changes (pipeline untouched).

---

## Self-review
**Spec coverage:** SQLite persistence (T1) ✓; Bearer auth + admin (T2,T6) ✓; in-process queue+worker+recovery (T3) ✓; `/v1` endpoints create/status/events/artifacts/cancel (T4,T5) ✓; healthz/readyz (T5) ✓; `/api` untouched, `/v1` wraps services (all tasks) ✓; deployment script + lifespan (T7) ✓; no CAD/frontend/benchmark change (Global Constraints, T8 diff check) ✓.
**Placeholder scan:** none — full code each step.
**Type consistency:** `db.*` signatures consistent T1↔T3↔T4; `auth.require_user→user_id:str`, `require_admin→bool`; `JobQueue.enqueue/depth/alive/recover/start/stop` consistent T3↔T5↔T7; routes use `app.state.db`/`app.state.queue` set in T7 lifespan (tests inject their own).
