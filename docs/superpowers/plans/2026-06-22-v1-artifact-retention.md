# Artifact Retention (P2) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Expire artifact directories of old terminal jobs (and orphan dirs) under `PROJECTS_ROOT` — startup + periodic + admin-triggered — preserving active/recent jobs and job rows, with a hard min-age floor, per-sweep delete cap, dry-run default, and 410-Gone API semantics. No schema change.

**Architecture:** New pure-ish module `backend/app/v1/retention.py` (`sweep(conn, *, dry_run, overrides, now)` → `SweepStats`, eligibility + orphan scan + containment-guarded `rmtree` + byte accounting). `routes.py` gains 410 inference on artifact endpoints, an `artifacts_available` field on the job view, and an admin sweep endpoint. `main.py` lifespan runs a startup sweep + a periodic asyncio task. Config knobs only.

**Tech Stack:** Python 3.11 (`/root/anaconda3/envs/cadskills/bin/python`), FastAPI, sqlite3, pytest.

## Global Constraints
- Engine frozen: **never** modify `backend/app/services` or `backend/app/orchestrator` — STOP/BLOCKED if a task seems to need it. Guard `git diff v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator` must stay empty.
- No CAD/frontend/benchmark/**schema** changes. No new SQL columns/tables. `db.py` query helpers may be added but no DDL change.
- Edits ONLY in `backend/app/v1/retention.py` (new), `backend/app/v1/routes.py`, `backend/app/v1/models.py`, `backend/app/main.py`, `backend/app/core/config.py`, `tests/test_v1_*`.
- Run from product root with cadskills python. Guard-check each commit.
- Job rows are **preserved** on deletion (history + quota intact). Only directories are removed.
- Terminal statuses: `completed`, `failed`, `cancelled`. Active (never eligible): `pending`, `running`.
- Retention windows (days, env): `API_RETENTION_COMPLETED_DAYS=7`, `API_RETENTION_FAILED_DAYS=3`, `API_RETENTION_CANCELLED_DAYS=1`. Floor `API_RETENTION_MIN_AGE_S=3600` (nothing younger deleted, even with override). Cap `API_RETENTION_MAX_DELETE=1000` per sweep. Interval `API_RETENTION_SWEEP_INTERVAL_S=3600`. Switch `API_RETENTION_ENABLED` (default 1).
- Containment guard before any `rmtree`: the resolved target must be a **direct child** of `config.PROJECTS_ROOT.resolve()` (mirror of the download-path guard). Never delete `PROJECTS_ROOT` itself or `api.db*`.
- 410 inference: owned terminal job + project_dir absent → `410 {detail:"artifacts expired", purged:true}`. Unknown/cross-user → 404. Live dir → unchanged.

---

## Task 1 — config knobs + `retention.py` core (unit-tested)

**Files:**
- Modify: `backend/app/core/config.py`
- Create: `backend/app/v1/retention.py`
- Test: `tests/test_v1_retention_unit.py`

**Interfaces — Produces:**
- config ints/bool: `API_RETENTION_ENABLED`, `API_RETENTION_COMPLETED_DAYS`, `API_RETENTION_FAILED_DAYS`, `API_RETENTION_CANCELLED_DAYS`, `API_RETENTION_MIN_AGE_S`, `API_RETENTION_SWEEP_INTERVAL_S`, `API_RETENTION_MAX_DELETE`.
- `retention.SweepStats` dataclass `(dry_run:bool, scanned:int, eligible:int, deleted:int, reclaimed_bytes:int, capped:bool, by_status:dict)`.
- `retention.sweep(conn, *, dry_run: bool = True, overrides: dict | None = None, now: float | None = None) -> SweepStats`.
- `retention._dir_size(path) -> int`, `retention._safe_under_root(path) -> bool` (helpers; tested indirectly).

- [ ] **Step 1: Write the failing test**
```python
# tests/test_v1_retention_unit.py
import sys, os, sqlite3, time
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest
from app.v1 import retention as rt
from app.core import config as cfg

DAY = 86400


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE jobs(job_id TEXT PRIMARY KEY, user_id TEXT, project_id TEXT,
                 status TEXT, stage TEXT, failure_class TEXT, created_at TEXT,
                 started_at TEXT, completed_at TEXT, queue_pos INTEGER, metrics_json TEXT)""")
    return c


def _job(c, job_id, project_id, status, completed_age_s, now):
    from datetime import datetime, timezone
    completed_at = (None if completed_age_s is None
                    else datetime.fromtimestamp(now - completed_age_s, tz=timezone.utc).isoformat())
    c.execute("INSERT INTO jobs(job_id,user_id,project_id,status,completed_at) VALUES(?,?,?,?,?)",
              (job_id, "u1", project_id, status, completed_at))
    c.commit()


def _mkdir(root, name, age_s=None, now=None):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "out.step").write_text("x" * 100)
    if age_s is not None:
        t = now - age_s
        os.utime(d, (t, t))
    return d


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "PROJECTS_ROOT", tmp_path / "projects")
    (tmp_path / "projects").mkdir()
    monkeypatch.setattr(cfg, "API_RETENTION_COMPLETED_DAYS", 7)
    monkeypatch.setattr(cfg, "API_RETENTION_FAILED_DAYS", 3)
    monkeypatch.setattr(cfg, "API_RETENTION_CANCELLED_DAYS", 1)
    monkeypatch.setattr(cfg, "API_RETENTION_MIN_AGE_S", 3600)
    monkeypatch.setattr(cfg, "API_RETENTION_MAX_DELETE", 1000)
    return cfg.PROJECTS_ROOT


def test_completed_over_window_eligible_and_deleted(env, monkeypatch):
    now = 1_000_000.0
    c = _conn()
    _job(c, "j1", "p1", "completed", 8 * DAY, now)        # > 7d -> eligible
    _mkdir(env, "p1")
    stats = rt.sweep(c, dry_run=False, now=now)
    assert stats.deleted == 1 and stats.by_status["completed"] == 1
    assert not (env / "p1").exists()
    assert stats.reclaimed_bytes >= 100
    # row preserved
    assert c.execute("SELECT 1 FROM jobs WHERE job_id='j1'").fetchone() is not None


def test_completed_under_window_preserved(env):
    now = 1_000_000.0
    c = _conn()
    _job(c, "j1", "p1", "completed", 2 * DAY, now)        # < 7d
    _mkdir(env, "p1")
    stats = rt.sweep(c, dry_run=False, now=now)
    assert stats.deleted == 0 and (env / "p1").exists()


def test_failed_and_cancelled_windows(env):
    now = 1_000_000.0
    c = _conn()
    _job(c, "jf", "pf", "failed", 4 * DAY, now)           # > 3d -> eligible
    _job(c, "jc", "pc", "cancelled", 2 * DAY, now)        # > 1d -> eligible
    _job(c, "jf2", "pf2", "failed", 1 * DAY, now)         # < 3d -> keep
    for n in ("pf", "pc", "pf2"):
        _mkdir(env, n)
    stats = rt.sweep(c, dry_run=False, now=now)
    assert not (env / "pf").exists() and not (env / "pc").exists()
    assert (env / "pf2").exists()
    assert stats.by_status["failed"] == 1 and stats.by_status["cancelled"] == 1


def test_active_never_eligible(env):
    now = 1_000_000.0
    c = _conn()
    _job(c, "jp", "pp", "pending", None, now)
    _job(c, "jr", "pr", "running", None, now)
    _mkdir(env, "pp"); _mkdir(env, "pr")
    stats = rt.sweep(c, dry_run=False, now=now)
    assert stats.deleted == 0 and (env / "pp").exists() and (env / "pr").exists()


def test_min_age_floor_blocks_even_with_override(env):
    now = 1_000_000.0
    c = _conn()
    _job(c, "j1", "p1", "completed", 1800, now)           # 30 min old
    _mkdir(env, "p1")
    # override completed window to 0 days, but floor 3600s must still protect it
    stats = rt.sweep(c, dry_run=False, overrides={"completed": 0}, now=now)
    assert stats.deleted == 0 and (env / "p1").exists()


def test_orphan_dir_eligible_by_mtime(env):
    now = 1_000_000.0
    c = _conn()
    # no job row references 'orphan'
    _mkdir(env, "orphan", age_s=2 * DAY, now=now)
    _mkdir(env, "fresh_orphan", age_s=600, now=now)       # < min age -> keep
    stats = rt.sweep(c, dry_run=False, now=now)
    assert not (env / "orphan").exists() and (env / "fresh_orphan").exists()
    assert stats.by_status["orphan"] == 1


def test_dry_run_reports_but_deletes_nothing(env):
    now = 1_000_000.0
    c = _conn()
    _job(c, "j1", "p1", "completed", 8 * DAY, now)
    _mkdir(env, "p1")
    stats = rt.sweep(c, dry_run=True, now=now)
    assert stats.dry_run is True and stats.eligible == 1 and stats.deleted == 0
    assert stats.reclaimed_bytes >= 100 and (env / "p1").exists()


def test_max_delete_cap(env, monkeypatch):
    now = 1_000_000.0
    monkeypatch.setattr(cfg, "API_RETENTION_MAX_DELETE", 2)
    c = _conn()
    for i in range(5):
        _job(c, f"j{i}", f"p{i}", "completed", 8 * DAY, now)
        _mkdir(env, f"p{i}")
    stats = rt.sweep(c, dry_run=False, now=now)
    assert stats.deleted == 2 and stats.capped is True
    remaining = sum((env / f"p{i}").exists() for i in range(5))
    assert remaining == 3
```

- [ ] **Step 2: Run, verify fail** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_retention_unit.py -v`
Expected: FAIL (`No module named 'app.v1.retention'`).

- [ ] **Step 3: Implement config** — add to `backend/app/core/config.py` after the rate-limit block:
```python
# --- /v1 artifact retention (P2) ---
API_RETENTION_ENABLED = _flag("API_RETENTION_ENABLED", "1")
API_RETENTION_COMPLETED_DAYS = int(os.environ.get("API_RETENTION_COMPLETED_DAYS", "7"))
API_RETENTION_FAILED_DAYS = int(os.environ.get("API_RETENTION_FAILED_DAYS", "3"))
API_RETENTION_CANCELLED_DAYS = int(os.environ.get("API_RETENTION_CANCELLED_DAYS", "1"))
API_RETENTION_MIN_AGE_S = int(os.environ.get("API_RETENTION_MIN_AGE_S", "3600"))
API_RETENTION_SWEEP_INTERVAL_S = int(os.environ.get("API_RETENTION_SWEEP_INTERVAL_S", "3600"))
API_RETENTION_MAX_DELETE = int(os.environ.get("API_RETENTION_MAX_DELETE", "1000"))
```

- [ ] **Step 4: Implement `retention.py`** — create `backend/app/v1/retention.py`:
```python
"""Artifact retention sweep for the /v1 surface (P2).

DB jobs table is the source of truth; project directories under
config.PROJECTS_ROOT are deleted, job rows are preserved. Single-instance.
"""
from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.core import config

DAY = 86400
_TERMINAL = ("completed", "failed", "cancelled")


@dataclass
class SweepStats:
    dry_run: bool
    scanned: int = 0
    eligible: int = 0
    deleted: int = 0
    reclaimed_bytes: int = 0
    capped: bool = False
    by_status: dict = field(default_factory=lambda: {
        "completed": 0, "failed": 0, "cancelled": 0, "orphan": 0})


def _window_days(status: str, overrides: dict | None) -> int:
    if overrides and status in overrides:
        return int(overrides[status])
    return {
        "completed": config.API_RETENTION_COMPLETED_DAYS,
        "failed": config.API_RETENTION_FAILED_DAYS,
        "cancelled": config.API_RETENTION_CANCELLED_DAYS,
    }[status]


def _completed_epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _safe_under_root(path: Path, root: Path) -> bool:
    """True iff path is a direct child of root (resolved), not root itself."""
    try:
        rp = path.resolve()
    except OSError:
        return False
    return rp.parent == root.resolve() and rp != root.resolve()


def sweep(conn, *, dry_run: bool = True, overrides: dict | None = None,
          now: float | None = None) -> SweepStats:
    t = time.time() if now is None else now
    root = config.PROJECTS_ROOT
    floor = config.API_RETENTION_MIN_AGE_S
    cap = config.API_RETENTION_MAX_DELETE
    stats = SweepStats(dry_run=dry_run)
    if not root.exists():
        return stats

    rows = conn.execute(
        "SELECT job_id, project_id, status, completed_at FROM jobs").fetchall()
    known_pids = {r["project_id"] for r in rows if r["project_id"]}
    targets: list[tuple[Path, str]] = []   # (dir, by_status key)

    # 1. terminal jobs past their window + floor
    for r in rows:
        status = r["status"]
        if status not in _TERMINAL or not r["project_id"]:
            continue
        ce = _completed_epoch(r["completed_at"])
        if ce is None:
            continue
        age = t - ce
        window_s = max(_window_days(status, overrides) * DAY, floor)
        if age < window_s:
            continue
        d = root / r["project_id"]
        if d.is_dir() and _safe_under_root(d, root):
            targets.append((d, status))

    # 2. orphan dirs (no job references the name) older than floor
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name in known_pids:
            continue
        try:
            age = t - os.path.getmtime(child)
        except OSError:
            continue
        if age >= floor and _safe_under_root(child, root):
            targets.append((child, "orphan"))

    stats.scanned = len(rows) + sum(1 for c in root.iterdir() if c.is_dir())
    stats.eligible = len(targets)

    for d, key in targets:
        if stats.deleted >= cap:
            stats.capped = True
            break
        size = _dir_size(d)
        if not dry_run:
            try:
                shutil.rmtree(d)
            except OSError:
                continue
        stats.deleted += 1
        stats.reclaimed_bytes += size
        stats.by_status[key] = stats.by_status.get(key, 0) + 1

    if dry_run:
        # report would-delete counts without having deleted
        stats.deleted = 0
    return stats
```
Note: in dry-run, the loop still tallies `by_status`/`reclaimed_bytes`/`eligible` for the would-delete set, then `deleted` is reset to 0 at the end (deletes nothing). The `cap` check still applies so dry-run reflects the capped set too.

- [ ] **Step 5: Run, verify pass** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_retention_unit.py -v` → all pass.

- [ ] **Step 6: Engine-freeze guard + commit**
```bash
cd /root/all_project_models/alfaj/text-to-cad-product
git diff --stat v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator   # must be empty
git add backend/app/core/config.py backend/app/v1/retention.py tests/test_v1_retention_unit.py
git commit -m "feat(v1): artifact retention sweep core + config knobs (P2)"
```

**Success:** per-status windows + floor + cap + orphan + dry-run all correct; rows preserved; containment guard; guard empty.

---

## Task 2 — API: 410 inference, `artifacts_available`, admin sweep endpoint

**Files:**
- Modify: `backend/app/v1/models.py` (add `artifacts_available` to `JobView`)
- Modify: `backend/app/v1/routes.py`
- Test: `tests/test_v1_retention_api.py`

**Interfaces — Consumes:** `retention.sweep`, `paths.project_dir`, `auth.require_admin`, `db.get_conn`, `_owned_row`. **Produces:** 410 on purged artifacts; `JobView.artifacts_available: bool`; `POST /v1/admin/retention/sweep`.

- [ ] **Step 1: Write the failing test**
```python
# tests/test_v1_retention_api.py
import sys, os
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.v1 import db, routes, auth
from app.core import config as cfg


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "API_DB_PATH", str(tmp_path / "r.db"))
    monkeypatch.setattr(cfg, "PROJECTS_ROOT", tmp_path / "projects")
    (tmp_path / "projects").mkdir()
    monkeypatch.setattr(cfg, "ADMIN_API_KEY", "admin-secret")
    c = db.connect(str(tmp_path / "r.db")); db.init_db(c)
    uid = db.create_user(c, "u1")
    key, _pfx, _kid, _uid = auth.mint_key(c, "u1")  # returns (key, prefix, kid, user_id)
    monkeypatch.setattr("app.v1.routes._OWNER_FOR_TESTS", None, raising=False)
    c.commit(); c.close()
    app = FastAPI()
    app.include_router(routes.router)
    tc = TestClient(app)
    tc._key = key
    tc._uid = _uid
    tc._projects = tmp_path / "projects"
    tc._dbpath = str(tmp_path / "r.db")
    return tc


def _insert_job(dbpath, job_id, uid, pid, status, completed_at="2020-01-01T00:00:00+00:00"):
    c = db.connect(dbpath)
    c.execute("INSERT INTO jobs(job_id,user_id,project_id,status,created_at,completed_at) "
              "VALUES(?,?,?,?,?,?)", (job_id, uid, pid, status, "2020-01-01T00:00:00+00:00", completed_at))
    c.commit(); c.close()


def _h(tc):
    return {"Authorization": f"Bearer {tc._key}"}


def test_artifacts_410_when_purged(client):
    _insert_job(client._dbpath, "j1", client._uid, "p1", "completed")
    # no project dir on disk -> purged
    r = client.get("/v1/jobs/j1/artifacts", headers=_h(client))
    assert r.status_code == 410
    body = r.json()
    assert body["detail"] == "artifacts expired" and body["purged"] is True


def test_download_410_when_purged(client):
    _insert_job(client._dbpath, "j1", client._uid, "p1", "completed")
    r = client.get("/v1/jobs/j1/artifacts/out.step", headers=_h(client))
    assert r.status_code == 410


def test_artifacts_live_dir_not_410(client):
    _insert_job(client._dbpath, "j1", client._uid, "p1", "completed")
    (client._projects / "p1").mkdir()
    r = client.get("/v1/jobs/j1/artifacts", headers=_h(client))
    assert r.status_code == 200


def test_unknown_job_404_not_410(client):
    r = client.get("/v1/jobs/nope/artifacts", headers=_h(client))
    assert r.status_code == 404


def test_job_view_artifacts_available(client):
    _insert_job(client._dbpath, "j1", client._uid, "p1", "completed")
    r = client.get("/v1/jobs/j1", headers=_h(client))
    assert r.status_code == 200 and r.json()["artifacts_available"] is False
    (client._projects / "p1").mkdir()
    r = client.get("/v1/jobs/j1", headers=_h(client))
    assert r.json()["artifacts_available"] is True


def test_admin_sweep_dry_run_default(client):
    _insert_job(client._dbpath, "j1", client._uid, "p1", "completed")
    (client._projects / "p1").mkdir()
    r = client.post("/v1/admin/retention/sweep",
                    headers={"Authorization": "Bearer admin-secret"}, json={})
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True and body["deleted"] == 0
    assert (client._projects / "p1").exists()        # nothing deleted


def test_admin_sweep_real_deletes(client):
    _insert_job(client._dbpath, "j1", client._uid, "p1", "completed")
    (client._projects / "p1").mkdir()
    r = client.post("/v1/admin/retention/sweep",
                    headers={"Authorization": "Bearer admin-secret"},
                    json={"dry_run": False})
    assert r.status_code == 200 and r.json()["deleted"] == 1
    assert not (client._projects / "p1").exists()


def test_admin_sweep_requires_admin(client):
    r = client.post("/v1/admin/retention/sweep", headers=_h(client), json={})
    assert r.status_code == 403
```

- [ ] **Step 2: Run, verify fail** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_retention_api.py -v`
Expected: FAIL (404 instead of 410; no `artifacts_available`; no admin route).

- [ ] **Step 3: Add `artifacts_available` to `JobView`** — in `backend/app/v1/models.py`, add an optional field to the `JobView` model (default None so existing constructions stay valid):
```python
    artifacts_available: bool | None = None
```
(Place it among the other JobView fields.)

- [ ] **Step 4: Implement routes changes** — in `backend/app/v1/routes.py`:

(a) add imports near the top (with the other app imports):
```python
from app.v1 import retention
from pydantic import BaseModel
```
(`BaseModel` may already be imported via models; if so, skip the duplicate. `Field` not needed.)

(b) a small helper after `_owned_row`:
```python
def _purged(r) -> bool:
    """Terminal job whose project dir no longer exists -> artifacts expired."""
    return (r["status"] in ("completed", "failed", "cancelled")
            and not paths.project_dir(r["project_id"]).exists())
```

(c) in `get_job`, set `artifacts_available`:
```python
    return JobView(job_id=r["job_id"], status=r["status"], stage=r["stage"],
                   failure_class=r["failure_class"],
                   queue_pos=db.pending_position(conn, job_id),
                   created_at=r["created_at"], started_at=r["started_at"],
                   completed_at=r["completed_at"],
                   artifacts_available=paths.project_dir(r["project_id"]).exists())
```

(d) in `list_artifacts`, before listing:
```python
    r = _owned_row(conn, job_id, user_id)
    if _purged(r):
        raise HTTPException(status_code=410, detail="artifacts expired",
                            headers=None)
    listing = artifact_service.list_artifacts(r["project_id"])
```
But `HTTPException` cannot carry an extra body field directly. Return a `JSONResponse` instead for the 410 (so `purged:true` is in the body):
```python
    r = _owned_row(conn, job_id, user_id)
    if _purged(r):
        return JSONResponse(status_code=410,
                            content={"detail": "artifacts expired", "purged": True})
    listing = artifact_service.list_artifacts(r["project_id"])
```
(`JSONResponse` is already imported in routes.py.)

(e) in `download_artifact`, after `_owned_row`, before building `root`:
```python
    r = _owned_row(conn, job_id, user_id)
    if _purged(r):
        return JSONResponse(status_code=410,
                            content={"detail": "artifacts expired", "purged": True})
    root = paths.project_dir(r["project_id"]).resolve()
```

(f) add the admin endpoint near the other `/admin/*` routes:
```python
class _RetentionSweepReq(BaseModel):
    dry_run: bool = True
    overrides: dict | None = None

@router.post("/admin/retention/sweep")
def admin_retention_sweep(body: _RetentionSweepReq,
                          _admin: bool = Depends(auth.require_admin),
                          conn=Depends(db.get_conn)):
    if not config.API_RETENTION_ENABLED:
        return {"enabled": False, "dry_run": body.dry_run, "scanned": 0,
                "eligible": 0, "deleted": 0, "reclaimed_bytes": 0,
                "capped": False, "by_status": {}}
    s = retention.sweep(conn, dry_run=body.dry_run, overrides=body.overrides)
    return {"enabled": True, "dry_run": s.dry_run, "scanned": s.scanned,
            "eligible": s.eligible, "deleted": s.deleted,
            "reclaimed_bytes": s.reclaimed_bytes, "capped": s.capped,
            "by_status": s.by_status}
```
(`config` is already imported in routes.py. `auth.require_admin` returns True or raises 403.)

- [ ] **Step 5: Run, verify pass** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_retention_api.py -v` → all pass.

- [ ] **Step 6: Engine-freeze guard + commit**
```bash
git diff --stat v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator   # must be empty
git add backend/app/v1/models.py backend/app/v1/routes.py tests/test_v1_retention_api.py
git commit -m "feat(v1): 410 on purged artifacts + artifacts_available + admin retention sweep (P2)"
```

**Success:** 410 on purged terminal job, 404 on unknown/cross-user, 200 on live; `artifacts_available` reflects dir presence; admin sweep dry-run default + real delete + 403 for non-admin.

---

## Task 3 — lifecycle wiring (startup + periodic) in `main.py`

**Files:**
- Modify: `backend/app/main.py`
- Modify (regression safety): `tests/test_v1_cors.py`, `tests/test_v1_integration.py`
- Test: `tests/test_v1_retention_lifecycle.py`

**Interfaces — Consumes:** `retention.sweep`, `v1db.connect`, `config.API_RETENTION_ENABLED`, `config.API_RETENTION_SWEEP_INTERVAL_S`. **Produces:** startup sweep + periodic background task in lifespan, gated and cancellable.

- [ ] **Step 1: Write the failing test**
```python
# tests/test_v1_retention_lifecycle.py
import sys, asyncio
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import importlib
import pytest
from app.core import config as cfg


def test_startup_sweep_called(tmp_path, monkeypatch):
    monkeypatch.setenv("API_DB_PATH", str(tmp_path / "r.db"))
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("API_KEY_SALT", "test-retention-salt")
    monkeypatch.setenv("API_RATE_LIMIT_ENABLED", "0")
    monkeypatch.setenv("API_RETENTION_ENABLED", "1")
    monkeypatch.setenv("API_RETENTION_SWEEP_INTERVAL_S", "999999")  # no periodic fire in test
    import app.core.config as c2; importlib.reload(c2)
    import app.v1.retention as rt2; importlib.reload(rt2)
    calls = []
    monkeypatch.setattr(rt2, "sweep", lambda conn, **kw: calls.append(kw) or rt2.SweepStats(dry_run=kw.get("dry_run", True)))
    import app.main as m; importlib.reload(m)
    from fastapi.testclient import TestClient
    with TestClient(m.app):
        pass    # entering context runs lifespan startup; exiting runs shutdown
    assert any(kw.get("dry_run") is False for kw in calls)   # startup sweep ran (real)


def test_disabled_no_startup_sweep(tmp_path, monkeypatch):
    monkeypatch.setenv("API_DB_PATH", str(tmp_path / "r.db"))
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("API_KEY_SALT", "test-retention-salt")
    monkeypatch.setenv("API_RATE_LIMIT_ENABLED", "0")
    monkeypatch.setenv("API_RETENTION_ENABLED", "0")
    import app.core.config as c2; importlib.reload(c2)
    import app.v1.retention as rt2; importlib.reload(rt2)
    calls = []
    monkeypatch.setattr(rt2, "sweep", lambda conn, **kw: calls.append(kw) or rt2.SweepStats(dry_run=True))
    import app.main as m; importlib.reload(m)
    from fastapi.testclient import TestClient
    with TestClient(m.app):
        pass
    assert calls == []
```

- [ ] **Step 2: Run, verify fail** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_retention_lifecycle.py -v`
Expected: FAIL (no sweep wired).

- [ ] **Step 3: Implement lifespan wiring** — in `backend/app/main.py`:

(a) add import with the other `app.v1` imports:
```python
from app.v1 import db as v1db, routes as v1routes, retention as v1retention
```

(b) inside `_lifespan`, after `app.state.queue = q` and before `try:`, add the startup sweep + periodic task:
```python
    app.state.queue = q
    _retention_task = None
    if config.API_RETENTION_ENABLED:
        try:
            rc = v1db.connect(); v1retention.sweep(rc, dry_run=False); rc.close()
        except Exception:
            pass

        async def _retention_loop():
            while True:
                await asyncio.sleep(config.API_RETENTION_SWEEP_INTERVAL_S)
                try:
                    rc = v1db.connect(); v1retention.sweep(rc, dry_run=False); rc.close()
                except Exception:
                    pass

        _retention_task = asyncio.create_task(_retention_loop())
    try:
        yield
    finally:
        if _retention_task is not None:
            _retention_task.cancel()
        await q.stop()
        await claude_code_adapter.shutdown()
```
(c) add `import asyncio` at the top of `main.py` if not present.

- [ ] **Step 4: Run, verify pass** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_retention_lifecycle.py -v` → pass.

- [ ] **Step 5: Regression safety for `main.app` tests** — `test_v1_cors.py` and `test_v1_integration.py` start `main.app`; disable retention so their temp dirs aren't swept. Add beside the existing env each sets (the same place they set `API_RATE_LIMIT_ENABLED=0`):
  - `test_v1_cors.py` `_reload()`: `os.environ["API_RETENTION_ENABLED"] = "0"`
  - `test_v1_integration.py` fixture: `monkeypatch.setenv("API_RETENTION_ENABLED", "0")`

- [ ] **Step 6: Run mw + main.app regressions** —
```bash
cd /root/all_project_models/alfaj/text-to-cad-product
/root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_retention_lifecycle.py tests/test_v1_cors.py tests/test_v1_integration.py -v
```
Expected: all pass.

- [ ] **Step 7: Engine-freeze guard + commit**
```bash
git diff --stat v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator   # must be empty
git add backend/app/main.py tests/test_v1_retention_lifecycle.py tests/test_v1_cors.py tests/test_v1_integration.py
git commit -m "feat(v1): startup + periodic retention sweep in lifespan (gated, cancellable) (P2)"
```

**Success:** startup sweep runs when enabled; skipped when disabled; periodic task created + cancelled on shutdown; CORS+integration suites green; guard empty.

---

## Task 4 — verification

**Files:** test only.
- [ ] **Step 1: /v1 suite** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_*.py -q` → all pass.
- [ ] **Step 2: full suite** — `/root/anaconda3/envs/cadskills/bin/python -m pytest tests/ -q` → all pass.
- [ ] **Step 3: engine-freeze guard** — `git diff --stat v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator` → empty.
- [ ] **Step 4: commit** (only if test-only fixups needed) `git commit -m "test(v1): artifact retention full verification"` (skip if nothing to commit).

**Success:** /v1 + full suites green; guard empty.

---

## Self-review
**Spec coverage:** per-status windows (T1 `_window_days`) ✓; min-age floor incl. override clamp (T1 `max(window_s, floor)`, test) ✓; MAX_DELETE cap + `capped` (T1) ✓; orphan-dir by mtime (T1) ✓; row preservation (T1, deletes dir only, test asserts row) ✓; containment guard `_safe_under_root` ✓; dry-run reports/deletes-nothing (T1) ✓; 410 + `purged:true` on artifacts+download (T2) ✓; 404 unknown/cross-user (T2, `_owned_row` raises 404) ✓; `artifacts_available` (T2, models+get_job) ✓; admin sweep endpoint dry-run default + overrides + disabled note + 403 (T2) ✓; startup + periodic + cancellable (T3) ✓; gated by ENABLED (T3) ✓; config knobs incl. all separate day vars + MAX_DELETE (T1) ✓; regression-safety for main.app tests (T3) ✓; no schema/CAD/frontend/benchmark/engine changes ✓.
**Placeholder scan:** none — all code concrete.
**Type consistency:** `SweepStats(dry_run,scanned,eligible,deleted,reclaimed_bytes,capped,by_status)` used identically T1↔T2 response mapping; `sweep(conn, *, dry_run, overrides, now)` signature matches all call sites (T2 endpoint omits `now`→time.time(); T3 omits `now`); `_purged(r)` / `_safe_under_root(path,root)` / `_window_days(status,overrides)` consistent. `auth.mint_key` returns `(key, prefix, kid, user_id)` per auth.py (T2 test unpacks 4).
