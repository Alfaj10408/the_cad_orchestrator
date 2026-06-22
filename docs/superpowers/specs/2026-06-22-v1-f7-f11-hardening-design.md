# Spec — F7 + F11 Hardening (P2)

**Date:** 2026-06-22
**Status:** Approved design → implementation plan
**Baseline:** `v0.2.6-artifact-retention`. Engine frozen (`v0.1-benchmark-10of10`).
**Goal:** F7 — reap orphaned headless Claude processes left by a crashed instance, at startup, with strict ownership matching. F11 — capture `metrics_json` + `failure_class` on every terminal path (incl. failures/cancels/crash-recovery) and expose an admin failure-summary endpoint. No schema change.

## Problem
- **F7:** On crash/SIGKILL/OOM/power-loss the in-memory `claude_code_adapter._running` registry is lost; spawned headless `claude` children reparent to init and keep running, silently draining the paid subscription + CPU. `queue.recover()` fixes DB `running` rows but not OS processes.
- **F11:** `metrics_json` is written only on the worker's mapped-terminal block; timeout/exception/cancelled/crash paths skip it, losing partial failure metrics. No aggregate failure visibility for operators.

## Locked decisions
- Reaper is **startup-only**, run **before** `queue.recover()`/`queue.start()` (no live job exists then → every match is a prior-instance orphan).
- **Triple-match** ownership check; default **ON** (`API_REAP_ORPHAN_CLAUDE=1`).
- **Unified terminal helper** in the queue routes all exit paths.
- **Admin failures endpoint** `GET /v1/admin/jobs/failures`.
- **No schema change**, no new persistent state (reaper is stateless OS-discovery; F11 reuses existing columns).
- Engine frozen: `claude_code_adapter.py`/`claude_generation.py`/`services/`/`orchestrator/` unchanged. No CAD/frontend/benchmark changes.
- `API_FAILURES_RECENT_LIMIT = 50`.
- Reaper log line includes **pid, cwd, create_time, runtime_seconds**.

## F7 design — orphan-Claude reaper
New `backend/app/v1/reaper.py`, uses `psutil` (7.2.2 present).
`reap_orphan_claude(*, dry_run: bool = False) -> ReapStats` iterates `psutil.process_iter`; a process is an orphan iff **all** hold:
1. `exe()` or `cmdline()[0]` == `config.CLAUDE_CODE_BINARY`;
2. cmdline contains both `-p` and the adjacent pair `--output-format stream-json` (headless signature — interactive `claude` sessions lack it);
3. `cwd()` resolves under `config.CLAUDE_CODE_WORKSPACE_ROOT`.

**Safety:**
- Skip own PID (`os.getpid()`) and its descendants.
- Per-process `try/except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess)` → skip.
- `SIGKILL` via `proc.kill()` then best-effort `proc.wait(timeout=3)`.
- **Log every kill** at INFO on logger `app.v1.reaper`: `pid`, `cwd`, `create_time` (UTC ISO), `runtime_seconds` (`now - create_time`). Dry-run logs the same with a "would-reap" marker.
- `API_REAP_ORPHAN_CLAUDE` (default 1) gate; `dry_run=True` lists matches without killing.
- The cwd-under-workspace clause is the hard guard against killing a developer's interactive `claude`.

**Ownership model:** "owned by a live job" = PID in `adapter._running` (current process, in-memory). At boot that set is empty → any triple-match is an orphan. Startup-only, so the reaper never needs the live set.

`ReapStats` = `{dry_run, scanned, matched, killed, errors}`.

**Crash recovery:** lifespan order → `reap_orphan_claude()` → `q.recover()` → `q.start()`. `recover()`'s crash-path write routes through the F11 unified helper (partial metrics + `failure_class="internal"`).

## F11 design — terminal metrics + visibility
- **Unified terminal write** in `queue.py`:
  `_terminal(conn, job_id, project_id, status, failure_class=None, stage=None)` — always attaches `metrics_json=_load_metrics(project_id)`, sets `failure_class`, `stage`, `completed_at`. **Every** exit path routes through it: success, timeout (`cad`), exception (`internal`), mapped-failure (mapped class), cancelled, and `recover()`'s crash path (`internal`).
- **Cancelled-mid-run:** replace the bare `continue` with `_terminal(..., status="cancelled")` so the cancelled row captures any partial metrics (status stays `cancelled`).
- **failure_class visibility:** already on `JobView` (unchanged).
- **Admin endpoint** `GET /v1/admin/jobs/failures` (`require_admin`):
  `{counts:{<failure_class>:n,...}, recent:[{job_id,status,failure_class,completed_at}]}`.
  `counts` = `failure_class` breakdown over `status IN (failed,cancelled)`; `recent` = last `limit` (query param, default `config.API_FAILURES_RECENT_LIMIT=50`) by `completed_at DESC`. Backed by a new **read-only** `db.failure_summary(conn, limit)` (SELECT + GROUP BY; no DDL). Under existing `/v1/admin/*` rate-limit `admin` category.

## Storage model
**Zero schema change, zero new persistent state.** Reaper = stateless `psutil` discovery at boot (no PID table/sidecar). F11 reuses existing `jobs.metrics_json` + `jobs.failure_class`; admin endpoint is a read aggregate; `recover()` keeps using the `jobs` table.

## Code areas (all outside frozen `services/`/`orchestrator/`)
- `backend/app/v1/reaper.py` — new: `reap_orphan_claude`, `ReapStats`, triple-match, logging.
- `backend/app/v1/queue.py` — `_terminal` helper; route all exit paths + cancelled + `recover()` through it.
- `backend/app/main.py` — startup reap (gated) before `recover()`/`start()`.
- `backend/app/v1/routes.py` — `GET /v1/admin/jobs/failures`.
- `backend/app/v1/db.py` — `failure_summary(conn, limit)` read helper (no DDL).
- `backend/app/core/config.py` — `API_REAP_ORPHAN_CLAUDE` (default 1), `API_FAILURES_RECENT_LIMIT=50`.
- tests.
- **No** engine, schema, frontend, benchmark changes.

## Config knobs (`config.py`)
`API_REAP_ORPHAN_CLAUDE` (default 1), `API_FAILURES_RECENT_LIMIT` (default 50).

## Testing
- **Reaper unit** (`psutil.process_iter` monkeypatched with fake proc objects exposing `pid/info/exe()/cmdline()/cwd()/create_time()/kill()/wait()`): triple-match → killed; binary-only / missing `stream-json` / cwd-outside-workspace → **not** killed; own-PID + descendant skipped; `NoSuchProcess`/`AccessDenied`/`ZombieProcess` swallowed; `dry_run` lists not kills; `API_REAP_ORPHAN_CLAUDE=0` → no-op; log line carries pid/cwd/create_time/runtime_seconds; `ReapStats` accounting.
- **Queue terminal unit:** timeout / exception / cancelled / mapped-failure each persist `metrics_json` (temp `component_metrics.json`) + correct `failure_class`; `recover()` crash path persists metrics + `internal`; success path unchanged.
- **Admin API:** `failures` → counts+recent (respects `limit`); non-admin 403; empty DB → `{counts:{}, recent:[]}`.
- **Lifecycle:** reaper called before `recover()`/`start()`, gated by `API_REAP_ORPHAN_CLAUDE`; `main.app` regression tests set `API_REAP_ORPHAN_CLAUDE=0`.
- /v1 + full suites green; engine-freeze guard empty.

## Non-goals
Periodic reaper (mid-run leak coverage); session-PID sidecar/exact ownership (engine frozen); cgroup/systemd supervision; SIGTERM-then-SIGKILL grace; time-series failure metrics; owner-job-view metrics block; multi-instance reaper coordination — all upgrade-path.

## Upgrade path
Periodic reaper excluding `adapter._running`; `runs/` sidecar PID record if engine un-freezes; cgroup/scope supervision; graceful SIGTERM→SIGKILL; failure time-series; per-owner metrics surfacing; host-scoped reaper for multi-instance.

## Release criteria
/v1 suite passes; full suite passes; engine-freeze guard `git diff v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator` empty; no CAD/frontend/benchmark/schema changes; startup-only triple-match reaper + unified terminal helper + admin failures endpoint all enforced; reaper logs pid/cwd/create_time/runtime_seconds.
