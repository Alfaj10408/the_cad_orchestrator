# P2 — F7 + F11 Hardening Status (2026-06-22)

Seventh P2 milestone complete. F7 — startup reaper kills orphaned headless Claude processes left by a crashed instance, with strict triple-match ownership. F11 — every terminal job path captures `metrics_json`+`failure_class`, plus an admin failure-summary endpoint. Engine frozen; CAD pipeline, schema, frontend, and benchmark unchanged. No schema change.

## Problem
- **F7:** On crash/SIGKILL/OOM/power-loss the in-memory `claude_code_adapter._running` registry is lost; spawned headless `claude` children reparent to init and keep running, silently draining the paid subscription + CPU. `queue.recover()` fixed DB `running` rows but not OS processes.
- **F11:** `metrics_json` was written only on the worker's mapped-terminal block; timeout/exception/cancelled/crash paths skipped it, losing partial failure metrics. No aggregate failure visibility for operators.

## Design (approved decisions)
- Reaper **startup-only**, run **before** `queue.recover()`/`queue.start()` (no live job exists then → every match is a prior-instance orphan).
- **Triple-match** ownership; default **ON** (`API_REAP_ORPHAN_CLAUDE=1`).
- **Unified terminal helper** routes all queue exit paths.
- **Admin failures endpoint** `GET /v1/admin/jobs/failures`.
- **No schema change**, no new persistent state (reaper stateless OS-discovery; F11 reuses existing columns).
- Engine frozen: `claude_code_adapter.py`/`claude_generation.py`/`services/`/`orchestrator/` untouched. `API_FAILURES_RECENT_LIMIT=50`. Reaper log includes pid/cwd/create_time/runtime_seconds.

## Implementation
- `config.py`: `API_REAP_ORPHAN_CLAUDE` (default 1), `API_FAILURES_RECENT_LIMIT` (default 50).
- `backend/app/v1/reaper.py` (new): `is_orphan_claude`, `reap_orphan_claude`, `ReapStats`, `_has_headless_sig`, `_cwd_under_workspace`, `_descendant_pids`; psutil-based.
- `backend/app/v1/queue.py`: `_terminal` helper; all exit paths + cancelled + `recover()` routed through it; `_load_metrics` uses `config.PROJECTS_ROOT` (prod-identical to prior `paths.project_dir`).
- `backend/app/v1/db.py`: `failure_summary(conn, limit)` read-only helper (no DDL).
- `backend/app/v1/routes.py`: `GET /v1/admin/jobs/failures`.
- `backend/app/main.py`: startup reap (gated, exceptions swallowed) before `recover()`/`start()`.

## F7 — orphan-Claude reaper architecture
Stateless `psutil` discovery at boot. `reap_orphan_claude(*, dry_run=False) -> ReapStats`. A process is an orphan iff **all** hold (true AND, early-return short-circuit):
1. `exe()`/`cmdline()[0]` == `config.CLAUDE_CODE_BINARY`;
2. cmdline contains `-p` **and** the adjacent pair `--output-format stream-json` (headless signature — interactive `claude` lacks it);
3. `cwd()` resolves under `config.CLAUDE_CODE_WORKSPACE_ROOT` — the **mandatory hard guard** against killing a developer's interactive session.

**Safety:** skip own PID + descendants (`os.getpid()` ∪ `_descendant_pids()`); per-process `try/except (NoSuchProcess, AccessDenied, ZombieProcess)`; `SIGKILL` (`proc.kill()`) + best-effort `proc.wait(timeout=3)`; exceptions swallowed at three layers (match-time, per-proc loop, main.py outer `try/except`) so boot can't crash; `API_REAP_ORPHAN_CLAUDE=0` → no-op; `dry_run` logs "would-reap" without killing. **Log every kill** at INFO on `app.v1.reaper`: `pid`, `cwd`, `create_time` (UTC ISO), `runtime_seconds`. `ReapStats` = `{dry_run, scanned, matched, killed, errors}`.

**Ownership model:** "owned by a live job" = PID in `adapter._running` (current process, in-memory). At boot that set is empty → any triple-match is a prior-instance orphan (reparented to init, not a current-process child, so not skip-listed → correctly reaped). Startup-only, no periodic reaper.

## F11 — failure metrics architecture
**Unified terminal write** in `queue.py`:
`_terminal(conn, job_id, project_id, status, failure_class=None, stage=None)` — always attaches `metrics_json=_load_metrics(project_id)`, sets `failure_class`, `stage`, `completed_at`. **Every** exit path routes through it: success (`completed`/None — unchanged), timeout (`failed/cad`), exception (`failed/internal`), mapped-failure (mapped class), cancelled-mid-run (stays `cancelled`, bare `continue` replaced), and `recover()`'s crash path (`failed/internal`). Partial `component_metrics.json` is now captured even on failure; missing file → NULL. `recover()` still re-queues pending jobs.

## Admin failures endpoint
`GET /v1/admin/jobs/failures` (`require_admin`, 403 non-admin), `limit` query param (default `config.API_FAILURES_RECENT_LIMIT=50`). Returns `{counts:{<failure_class>:n,...}, recent:[{job_id,status,failure_class,completed_at}]}`. Backed by read-only `db.failure_summary`: `counts` = GROUP BY `failure_class` (NULL→`'none'` via COALESCE) over `status IN (failed,cancelled)`; `recent` = same filter, `ORDER BY completed_at DESC LIMIT ?` (int-cast, bound param — injection-safe). Completed jobs excluded; empty DB → `{counts:{},recent:[]}`. Under existing `/v1/admin/*` rate-limit `admin` category.

## Startup recovery behavior
Lifespan order: F9 salt guard → `v1db.init_db` → **`reap_orphan_claude()`** (gated, swallowed) → `q = JobQueue()` → `q.recover()` (running→failed/internal via `_terminal`, re-queue pending) → `q.start()` → retention startup/periodic. Reaper precedes recover/start so it never touches a live job. `finally`: retention-task cancel → `q.stop()` → `claude_code_adapter.shutdown()`.

## Storage model
**Zero schema change, zero new persistent state.** Reaper = stateless `psutil` discovery (no PID table/sidecar). F11 reuses existing `jobs.metrics_json` + `jobs.failure_class`; admin endpoint is a read aggregate.

## Tests
- `test_v1_reaper_unit.py` (10): triple-match; binary-only/cwd-outside/wrong-binary not orphan; kills orphan; dry-run lists; self+descendant skipped; psutil errors swallowed; disabled no-op; log carries pid/cwd/create_time/runtime_seconds.
- `test_v1_queue_terminal.py` (3): failed+cancelled capture metrics+class; no-metrics-file → NULL.
- `test_v1_failures_api.py` (4): counts+recent; limit honored; non-admin 403; empty DB.
- `test_v1_reaper_lifecycle.py` (2): reaper invoked once at startup when enabled; not when disabled.
- Regression-safety (env-only): `test_v1_cors.py`, `test_v1_integration.py` set `API_REAP_ORPHAN_CLAUDE=0`.
- **/v1 suite: 95/95 · full suite: 139/139 · engine-freeze guard empty.**

## Review outcome (opus whole-branch)
**GO. No Critical/High.** Reaper safety verified: true-AND triple-match (each leg an early-return short-circuit; cwd clause mandatory), partial matches never killed (dedicated tests), own-PID+descendants skipped, exceptions swallowed at 3 layers, startup-only timing, `dry_run` never kills. F11: six paths through `_terminal`, semantics preserved, `_load_metrics` change prod-identical, `failure_summary` SELECT-only/injection-safe. No schema/engine changes; guard empty.
- **Minors (deferred):** descendant-skip + errors-count not independently unit-asserted; dead `import json` in queue.py; AccessDenied-match test doesn't assert errors count.

## Remaining roadmap (not started)
- **F10** — admin-key rotation/revocation (next recommended: cheap security hygiene).
- **Multi-worker prep** — strategic; atomic job claiming (`claimed_by`/lease), drop `recover()`'s "fail-all-running" assumption, advisory locks; introduces the first justified schema change. Own design proposal.
- **F4 follow-up** — monotonic insert-sequence tiebreak for `queue_pos`.
- **F7/F11 upgrade path** — periodic reaper excluding `adapter._running`; `runs/` sidecar PID record (if engine un-freezes); cgroup/systemd supervision; SIGTERM→SIGKILL grace; failure time-series; owner-job-view metrics block; host-scoped reaper for multi-instance.
- **Rate-limit / quota / retention upgrades** — Redis backends, per-key/per-user overrides, multi-instance coordination.
- **P3** — multi-worker scaling.

## Commits (off `5e47716`)
`645d7e2` reaper + config (F7) · `194b50f` unified terminal helper (F11) · `b507bc0` admin failures endpoint + read helper (F11) · `d754d5e` startup reaper wiring (F7).
Tag: `v0.2.7-f7-f11-hardening`.

*P2 F7+F11 hardening only. No CAD, frontend, benchmark, schema, or engine changes.*
