# Multi-Worker Activation (P3) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make `API_WORKER_MODE="claim"` a validated opt-in for N processes (one host, shared SQLite): claim observability (`GET /v1/admin/claims`), structured claim/renew/reclaim logs, a `WORKERS` launch knob, and an activation/rollback runbook — `API_WORKER_MODE="single"` stays the committed default.

**Architecture:** Read-only `db.active_claims` + an admin-gated `/v1/admin/claims` endpoint (claim view, derived from `jobs` columns). Structured INFO logs on `app.v1.queue` for claim/renew/reclaim. `serve_api.sh` gains `WORKERS` → `uvicorn --workers N`. A markdown runbook documents staged activation + drain-then-switch / abrupt rollback. Concurrency/fault-injection validation tests at the worker-loop level (no real multi-process uvicorn in pytest).

**Tech Stack:** Python 3.11 (`/root/anaconda3/envs/cadskills/bin/python`), FastAPI, sqlite3 (WAL), asyncio, pytest (no pytest-asyncio — drive via `asyncio.run`).

## Global Constraints
- Engine frozen: **never** modify `backend/app/services` or `backend/app/orchestrator` — STOP/BLOCKED otherwise. Guard `git diff v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator` must stay empty.
- **No schema change** (no DDL/new columns/tables). No CAD/frontend/benchmark changes.
- Edits ONLY in `backend/app/v1/db.py`, `backend/app/v1/routes.py`, `backend/app/v1/queue.py`, `scripts/serve_api.sh`, `docs/MULTIWORKER_ACTIVATION_RUNBOOK.md` (new), `tests/test_v1_*`.
- **`API_WORKER_MODE="single"` remains the committed default** (do not change the default).
- `/v1/admin/claims` = **active claims, NOT a process census**; idle workers absent (documented). Real worker-process registry deferred.
- Rollback: planned = drain-then-switch (zero loss); emergency = abrupt switch (running → failed/re-submittable). Both in the runbook.
- Concurrency: effective = `WORKERS × CLAUDE_CODE_MAX_CONCURRENT`; runbook mandates ≤ subscription limit (start N=2). No new global cap.
- Run from product root with cadskills python. Guard-check each commit.

---

## Task 1 — `active_claims` helper + `GET /v1/admin/claims`

**Files:**
- Modify: `backend/app/v1/db.py`, `backend/app/v1/routes.py`
- Test: `tests/test_v1_claims_api.py`

**Interfaces — Produces:** `db.active_claims(conn) -> list[sqlite3.Row]` (running jobs with claim columns); `GET /v1/admin/claims` → `{claims, by_owner, now}`.

- [ ] **Step 1: Write the failing test**
```python
# tests/test_v1_claims_api.py
import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
from datetime import datetime, timezone, timedelta
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.v1 import db, routes, auth
from app.core import config as cfg


def _iso(dt): return dt.isoformat()
def _now(): return datetime.now(timezone.utc)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "API_DB_PATH", str(tmp_path / "r.db"))
    monkeypatch.setattr(cfg, "ADMIN_API_KEY", "admin-secret")
    c = db.connect(str(tmp_path / "r.db")); db.init_db(c)
    key, _p, _k, _u = auth.mint_key(c, "u1")
    # two running claims (one fresh lease, one expired=stale) + one pending (excluded)
    fut = _iso(_now() + timedelta(seconds=100))
    past = _iso(_now() - timedelta(seconds=100))
    c.execute("INSERT INTO jobs(job_id,user_id,project_id,status,created_at,claimed_by,claimed_at,lease_expires_at) "
              "VALUES('jr1','u1','p1','running',?,?,?,?)", ("2020-01-01T00:00:00+00:00","wA","2020-01-01T00:00:00+00:00",fut))
    c.execute("INSERT INTO jobs(job_id,user_id,project_id,status,created_at,claimed_by,claimed_at,lease_expires_at) "
              "VALUES('jr2','u1','p2','running',?,?,?,?)", ("2020-01-01T00:00:01+00:00","wA","2020-01-01T00:00:01+00:00",past))
    c.execute("INSERT INTO jobs(job_id,user_id,project_id,status,created_at) "
              "VALUES('jp','u1','p3','pending',?)", ("2020-01-01T00:00:02+00:00",))
    c.commit(); c.close()
    app = FastAPI(); app.include_router(routes.router)
    tc = TestClient(app); tc._ukey = key
    return tc


def _admin(): return {"Authorization": "Bearer admin-secret"}


def test_claims_lists_running_with_stale_flag(client):
    r = client.get("/v1/admin/claims", headers=_admin())
    assert r.status_code == 200
    body = r.json()
    ids = {c["job_id"]: c for c in body["claims"]}
    assert set(ids) == {"jr1", "jr2"}          # pending excluded
    assert ids["jr1"]["stale"] is False         # future lease
    assert ids["jr2"]["stale"] is True          # past lease
    assert ids["jr1"]["claimed_by"] == "wA"
    assert "now" in body


def test_claims_by_owner_grouping(client):
    body = client.get("/v1/admin/claims", headers=_admin()).json()
    owners = {o["claimed_by"]: o for o in body["by_owner"]}
    assert owners["wA"]["running"] == 2


def test_claims_requires_admin(client):
    r = client.get("/v1/admin/claims", headers={"Authorization": f"Bearer {client._ukey}"})
    assert r.status_code == 403


def test_claims_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "API_DB_PATH", str(tmp_path / "e.db"))
    monkeypatch.setattr(cfg, "ADMIN_API_KEY", "admin-secret")
    c = db.connect(str(tmp_path / "e.db")); db.init_db(c); c.close()
    app = FastAPI(); app.include_router(routes.router)
    r = TestClient(app).get("/v1/admin/claims", headers={"Authorization": "Bearer admin-secret"})
    assert r.status_code == 200
    body = r.json()
    assert body["claims"] == [] and body["by_owner"] == []
```

- [ ] **Step 2: Run, verify fail** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_claims_api.py -v`
Expected: FAIL (no route / no `active_claims`).

- [ ] **Step 3: Implement `db.active_claims`** — add to `backend/app/v1/db.py` (read-only, no DDL):
```python
def active_claims(conn):
    """Running jobs with claim metadata (claim view; read-only)."""
    return conn.execute(
        "SELECT job_id, claimed_by, status, claimed_at, lease_expires_at "
        "FROM jobs WHERE status='running' ORDER BY claimed_at").fetchall()
```

- [ ] **Step 4: Implement the route** — in `backend/app/v1/routes.py`, near the other `/admin/*` routes (`datetime`/`timezone` already imported):
```python
@router.get("/admin/claims")
def admin_claims(_: bool = Depends(auth.require_admin), conn=Depends(db.get_conn)):
    now = datetime.now(timezone.utc).isoformat()
    rows = db.active_claims(conn)
    claims = []
    owners: dict = {}
    for r in rows:
        le = r["lease_expires_at"]
        stale = le is not None and le < now
        claims.append({"claimed_by": r["claimed_by"], "job_id": r["job_id"],
                       "status": r["status"], "claimed_at": r["claimed_at"],
                       "lease_expires_at": le, "stale": stale})
        o = owners.setdefault(r["claimed_by"], {"claimed_by": r["claimed_by"],
                                                "running": 0, "oldest_lease": le})
        o["running"] += 1
        if le is not None and (o["oldest_lease"] is None or le < o["oldest_lease"]):
            o["oldest_lease"] = le
    return {"claims": claims, "by_owner": list(owners.values()), "now": now}
```

- [ ] **Step 5: Run, verify pass** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_claims_api.py -v` → all pass.

- [ ] **Step 6: Engine-freeze guard + commit**
```bash
git diff --stat v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator   # empty
git add backend/app/v1/db.py backend/app/v1/routes.py tests/test_v1_claims_api.py
git commit -m "feat(v1): admin claims view (active claims, stale flag, by-owner) (multiworker activation)"
```

**Success:** running claims listed (pending excluded); `stale` reflects expired lease; `by_owner` counts; `now` present; non-admin 403; empty → `{claims:[],by_owner:[]}`; read-only/no DDL; guard empty.

---

## Task 2 — claim/renew/reclaim logs + concurrency/fault validation

**Files:**
- Modify: `backend/app/v1/queue.py`
- Test: `tests/test_v1_worker_activation.py`

**Interfaces — Consumes:** existing `_claim_worker`/`_heartbeat`/`recover` (claim branch), `db.reclaim_expired`. **Produces:** INFO logs on logger `app.v1.queue` for claim/renew/reclaim (behavior-neutral); validation tests for no-duplicate-execution + reclaim.

- [ ] **Step 1: Write the failing test**
```python
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
```

- [ ] **Step 2: Run, verify fail** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_worker_activation.py -v`
Expected: FAIL (no `claim`/`reclaim` log lines yet — log-assert tests fail; concurrency tests may already pass on built claim logic).

- [ ] **Step 3: Add the logger + log lines** — in `backend/app/v1/queue.py`:

(a) at the top with the imports add:
```python
import logging
_log = logging.getLogger("app.v1.queue")
```
(`import asyncio, json` line already present — add the two lines after the imports block.)

(b) in `_claim_worker`, immediately after a successful claim (`if not db.claim_job(...): continue`):
```python
            _log.info("claim worker=%s job=%s", self.worker_id, job_id)
```
(c) in `_heartbeat`, after `db.renew_lease(...)`:
```python
                _log.info("renew worker=%s job=%s", self.worker_id, job_id)
```
(d) in `recover()`'s claim branch, capture + log the count:
```python
            if self.mode == "claim":
                n = db.reclaim_expired(conn)
                _log.info("reclaim worker=%s count=%d", self.worker_id, n)
```
(Leave single-mode branch + all terminal/run logic unchanged.)

- [ ] **Step 4: Run, verify pass** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_worker_activation.py -v` → all pass.

- [ ] **Step 5: Regression — single mode + existing claim tests** —
```bash
cd /root/all_project_models/alfaj/text-to-cad-product
/root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_queue.py tests/test_v1_worker_claim.py tests/test_v1_worker_db.py -q
```
Expected: all pass (logs are behavior-neutral; single mode untouched).

- [ ] **Step 6: Engine-freeze guard + commit**
```bash
git diff --stat v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator   # empty
git add backend/app/v1/queue.py tests/test_v1_worker_activation.py
git commit -m "feat(v1): claim/renew/reclaim INFO logs + no-duplicate-execution validation (multiworker activation)"
```

**Success:** two workers run each job exactly once (no duplicate); claim/renew/reclaim emit INFO logs on `app.v1.queue`; reclaim requeues (NULL-lease→pending); single mode + existing claim tests green; guard empty.

---

## Task 3 — `WORKERS` launch knob + activation/rollback runbook + verification

**Files:**
- Modify: `scripts/serve_api.sh`
- Create: `docs/MULTIWORKER_ACTIVATION_RUNBOOK.md`
- Test: (verification only — shell + docs, no unit test)

- [ ] **Step 1: Add the `WORKERS` knob to `serve_api.sh`** — change the exec line (currently lines 13-15) to pass `--workers`:
```bash
export WORKERS="${WORKERS:-1}"
exec env LD_PRELOAD=/root/anaconda3/envs/cadskills/lib/libexpat.so.1 \
  /root/anaconda3/envs/cadskills/bin/uvicorn app.main:app \
  --app-dir backend --host 0.0.0.0 --port "${PORT:-8080}" --workers "${WORKERS}"
```
(Default `WORKERS=1` → identical to today. `API_WORKER_MODE` is read from env by the app; default stays `single`.)

- [ ] **Step 2: Sanity-check the script parses** — `cd /root/all_project_models/alfaj/text-to-cad-product && bash -n scripts/serve_api.sh && echo "syntax ok"`
Expected: `syntax ok` (do NOT launch the server).

- [ ] **Step 3: Write the runbook** — create `docs/MULTIWORKER_ACTIVATION_RUNBOOK.md` with these sections (concrete content, no placeholders):
```markdown
# Multi-Worker Activation Runbook

Default is `API_WORKER_MODE=single`, `WORKERS=1` (single in-process worker; current
production behavior). Claim mode is an opt-in; activate via the staged plan below.
SQLite single-host only — do not run workers across hosts (no shared DB).

## Preconditions
- `v0.2.9-multiworker-prep` foundation present (claim/lease/heartbeat/recovery).
- Effective concurrency = WORKERS × CLAUDE_CODE_MAX_CONCURRENT. Keep ≤ the Claude
  subscription session limit. Start at N=2.

## Stage 1 — 1 worker, claim mode
1. `export API_WORKER_MODE=claim WORKERS=1`
2. Restart: `./scripts/serve_api.sh`
3. Submit a job; confirm it reaches `completed` and `GET /v1/admin/claims` shows it
   under one `claimed_by` while running.
Gate: claim-mode outcomes match single mode (completed / cancel / timeout).

## Stage 2 — 2 workers
1. `export API_WORKER_MODE=claim WORKERS=2`; restart.
2. Submit several jobs. Watch `GET /v1/admin/claims` (`by_owner` shows 2 distinct
   `claimed_by`); watch logs for `claim`/`renew`/`reclaim` on `app.v1.queue`.
3. Crash test: `kill -9` one worker process mid-job. Its lease expires (~LEASE_S);
   the survivor reclaims (`reclaim count=…`) and the job re-runs; the orphaned
   `claude` child is killed by the F7 reaper on next boot.
Gate: zero duplicate executions; reclaim observed; no `database is locked`.

## Stage N — small N
Raise WORKERS one step at a time, keeping WORKERS × CLAUDE_CODE_MAX_CONCURRENT ≤
subscription limit. After each step, confirm throughput rises, leases stay renewed,
and the queue drains.

## Observability
- `GET /v1/admin/claims` (admin): active claims grouped by owner + `stale` flag +
  `now`. NOTE: this is a CLAIM view, not a process census — an idle worker holding
  no running job does NOT appear; `claimed_by` proves a held claim, not a live
  process (use `stale`). A real worker-process registry is a future milestone.
- Logs (`app.v1.queue`, INFO): `claim worker=… job=…`, `renew worker=… job=…`,
  `reclaim worker=… count=…`.

## Rollback
### Planned — drain-then-switch (zero lost work; PREFERRED)
1. Stop new submissions (take the node/LB out of rotation; running jobs keep finishing).
2. Wait until drained: `count_pending == 0` and `GET /v1/admin/claims` → `claims: []`.
3. `export API_WORKER_MODE=single WORKERS=1`; restart. Nothing is running, so single
   `recover()` fails nothing. Zero failures.

### Emergency — abrupt switch (fast, lossy-but-safe)
1. `export API_WORKER_MODE=single WORKERS=1`; restart immediately.
2. Single `recover()` fails-all-running → in-flight jobs become `failed`
   (re-submittable). No corruption, no stuck rows; claim columns are ignored in
   single mode.

Both rollbacks are config-only (no code or schema revert).

## Success criteria
- Stage 1 parity; Stage 2 zero-duplicate + crash→reclaim→re-run; Stage N throughput
  scales with concurrency ≤ subscription; `/v1/admin/claims` accurate; rollback
  validated (drain = zero failures, abrupt = failed/re-submittable).
```

- [ ] **Step 4: /v1 + full suite (default single mode) + guard**
```bash
cd /root/all_project_models/alfaj/text-to-cad-product
/root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_*.py -q       # all /v1
/root/anaconda3/envs/cadskills/bin/python -m pytest tests/ -q                   # full suite
git diff --stat v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator   # empty
```
Expected: all pass; guard empty.

- [ ] **Step 5: Commit**
```bash
git add scripts/serve_api.sh docs/MULTIWORKER_ACTIVATION_RUNBOOK.md
git commit -m "feat: WORKERS launch knob + multi-worker activation/rollback runbook (P3)"
```

**Success:** `serve_api.sh` accepts `WORKERS` (default 1 = today); runbook documents staged activation + drain-then-switch / abrupt rollback + observability + concurrency cap; /v1 + full suites green; guard empty.

---

## Self-review
**Spec coverage:** `/v1/admin/claims` active-claims view + stale flag + by_owner + now + idle-workers-absent documented (T1 + runbook) ✓; read-only `active_claims`, no DDL (T1) ✓; claim/renew/reclaim structured logs on `app.v1.queue` (T2) ✓; duplicate-execution detection via two-worker no-dup tests (T2) ✓; stale-lease/crash recovery exercised (T2 reclaim test; runbook crash procedure) ✓; split-brain note (runbook topology invariant) ✓; WORKERS → `uvicorn --workers` (T3) ✓; staged activation 1/2/N + safety + observability + rollback (drain-then-switch default, abrupt emergency) + concurrency cap + success criteria (T3 runbook) ✓; default stays single (T3 WORKERS=1, no mode-default change) ✓; no schema/CAD/frontend/benchmark/engine changes ✓.
**Placeholder scan:** none — endpoint code, log lines, shell, and runbook content all concrete.
**Type consistency:** `active_claims(conn)->list[Row]` (T1) consumed by the route; route returns `{claims,by_owner,now}` matching T1 tests; `_log = logging.getLogger("app.v1.queue")` used by all three log sites (T2) and asserted (T2 tests); `WORKERS` env consistent shell↔runbook. No default changes to `API_WORKER_MODE`.
