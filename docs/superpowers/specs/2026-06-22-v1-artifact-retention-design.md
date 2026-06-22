# Spec — Artifact Retention (P2)

**Date:** 2026-06-22
**Status:** Approved design → implementation plan
**Baseline:** `v0.2.5-rate-limiting`. Engine frozen (`v0.1-benchmark-10of10`).
**Goal:** Automatic disk lifecycle management — expire artifact directories of old terminal jobs (and orphan dirs) while preserving active and recent jobs, with admin-triggered dry-run and a hard min-age floor. No schema change.

## Problem
Generated artifacts (STEP/STL/GLB/reports/logs/project dirs) accumulate indefinitely under `PROJECTS_ROOT`, eventually tripping the F6 `disk` readiness check. No cleanup exists.

## Locked decisions
- **DB `jobs` table is source of truth** (`status` + `completed_at`); filesystem `paths.project_dir(project_id)` trees are owned/deleted by retention. **Job rows are preserved** (history + quota accounting intact).
- **Per-status windows**, separate env knobs (days): completed 7, failed 3, cancelled 1. `pending`/`running` never eligible.
- **Hard min-age floor** `API_RETENTION_MIN_AGE_S` (default 3600s): nothing younger deleted, **even with admin override**.
- **Per-sweep delete cap** `API_RETENTION_MAX_DELETE` (default 1000): a single sweep deletes at most this many dirs (bounds blast radius / IO).
- **Schema-free 410 inference:** terminal job + dir absent → `410 Gone {detail:"artifacts expired", purged:true}`. Unknown/cross-user → 404. Live dir → 200.
- **Triggers:** startup sweep + periodic background task + admin endpoint. Admin endpoint `dry_run=true` by default.
- No CAD/frontend/benchmark/schema changes; engine-freeze guard empty. `routes.py` changes are allowed (API facade, outside frozen dirs).

## Retention model
Age = `now − completed_at`. Eligible iff:
`status ∈ {completed,failed,cancelled}` AND `age ≥ window(status)` AND `age ≥ API_RETENTION_MIN_AGE_S` AND dir exists.

| status | window knob | default |
|---|---|---|
| completed | `API_RETENTION_COMPLETED_DAYS` | 7 |
| failed | `API_RETENTION_FAILED_DAYS` | 3 |
| cancelled | `API_RETENTION_CANCELLED_DAYS` | 1 |
| pending/running | — | never |

**Orphan dirs:** a direct child of `PROJECTS_ROOT` with no matching `jobs.project_id`, with dir mtime older than `API_RETENTION_MIN_AGE_S`, is eligible (cleans historical orphans). The `api.db*` files and `PROJECTS_ROOT` itself are never targets.

## Storage model
- **Eligibility** computed from `jobs` rows (join to dir presence) + an orphan scan of `PROJECTS_ROOT` children.
- **Deletion** = `shutil.rmtree(dir)` after a re-check (terminal status + age) and a containment guard: `dir.resolve()` must be a direct child of `config.PROJECTS_ROOT.resolve()` (mirror of the download-path guard). Row untouched.
- `completed_at` is UTC ISO (same writer as quotas); age computed by parsing it.

## Cleanup mechanism
Single function `retention.sweep(conn, *, dry_run: bool, overrides: dict|None=None) -> SweepStats` shared by all triggers.
- **Startup:** one `sweep(dry_run=False)` in lifespan, after queue start, guarded by `API_RETENTION_ENABLED`.
- **Periodic:** background `asyncio` task every `API_RETENTION_SWEEP_INTERVAL_S` (default 3600s) calling `sweep(dry_run=False)`; created in lifespan, cancelled on shutdown. Uses a short-lived DB connection per run.
- **Admin:** `POST /v1/admin/retention/sweep` (require_admin), body `{dry_run?=true, overrides?}` → `sweep`.
- `API_RETENTION_ENABLED` (default 1) gates startup + periodic (admin endpoint still callable, returns disabled note if off — see API). `API_RETENTION_MAX_DELETE` caps deletions per call; when hit, sweep stops and reports `capped:true`.

`SweepStats` = `{dry_run, scanned, eligible, deleted, reclaimed_bytes, capped, by_status:{completed,failed,cancelled,orphan}}`. Dry-run computes the same `eligible`/`reclaimed_bytes` but `deleted=0` and deletes nothing.

## API behavior
- `GET /v1/jobs/{id}/artifacts` and `/artifacts/{rel}`: if the owned job row is terminal and its project_dir is absent → **410** `{detail:"artifacts expired", purged:true}`. Unknown job or cross-user → 404 (unchanged). Live dir → unchanged behavior.
- `GET /v1/jobs/{id}`: 200 with terminal status preserved; **additive** derived field `artifacts_available: bool` (project_dir present). No schema change.
- Admin sweep response = `SweepStats` (plus `enabled` flag). When `API_RETENTION_ENABLED=0`, the admin endpoint returns `{enabled:false, ...zeroes}` without scanning (or honors an explicit `force`? — no: keep simple, just report disabled).

## Admin controls
- `POST /v1/admin/retention/sweep` — `require_admin`; `dry_run` defaults **true** (preview); `dry_run=false` deletes. `overrides` may set per-status day windows for this call, still clamped by `MIN_AGE_S`. Capped by `MAX_DELETE`.
- Stats returned for both dry-run and real (operators preview before committing).
- Rate limiting: this endpoint is `/v1/admin/*` → `admin` category (60/min), no new category.

## Interaction
- **Quotas:** rows persist → dynamic accounting (daily/in-flight) unaffected. Retention frees disk only.
- **Rate limiting:** admin sweep counted under `admin` bucket. No change to limiter.
- **F6 readiness `disk`:** retention lowers `STORAGE_ROOT` usage → helps `readyz.disk` stay green. Complementary, no coupling.

## Code areas (all outside frozen `services/`/`orchestrator/`)
- `backend/app/v1/retention.py` — new: `eligible_jobs`, orphan scan, `sweep`, `SweepStats`, containment guard, byte accounting.
- `backend/app/main.py` — startup sweep + periodic task (lifespan), gated.
- `backend/app/v1/routes.py` — 410 inference on artifact endpoints; `artifacts_available` on job view; admin sweep endpoint.
- `backend/app/core/config.py` — knobs (below).
- tests.
- **No** schema, `db.*` signature change beyond a read helper if needed, CAD, frontend, benchmark, queue.py-logic, or engine changes.

## Config knobs (`config.py`)
`API_RETENTION_ENABLED` (default 1), `API_RETENTION_COMPLETED_DAYS=7`, `API_RETENTION_FAILED_DAYS=3`, `API_RETENTION_CANCELLED_DAYS=1`, `API_RETENTION_MIN_AGE_S=3600`, `API_RETENTION_SWEEP_INTERVAL_S=3600`, `API_RETENTION_MAX_DELETE=1000`.

## Testing
- Eligibility unit: each terminal status under/over its window; pending/running never eligible; min-age floor blocks even with override; orphan-dir detection by mtime; `api.db` excluded.
- Sweep: deletes eligible, preserves active+recent+sub-floor; `dry_run` reports but deletes nothing; containment guard rejects a symlink/path escaping `PROJECTS_ROOT`; `MAX_DELETE` cap stops + `capped:true`; reclaimed-bytes > 0 on real delete.
- API: 410 on purged terminal job; 404 on unknown/cross-user; 200 on live; `artifacts_available` true/false; admin sweep dry-run (deleted=0) vs real (deleted>0); non-admin → 403; disabled → `enabled:false`.
- Regression: existing /v1 + full suites green (retention disabled in fixtures using `main.app`). Engine-freeze guard empty.

## Non-goals
Multi-instance sweep coordination/locking; per-user retention overrides; soft-delete/trash tier; object-store lifecycle; retention metrics endpoint; `purged_at` audit column — all upgrade-path. No quota/rate-limit changes.

## Upgrade path
DB advisory `retention_lock` so one instance sweeps in multi-worker; per-user overrides (mirror `user_quota`); soft-delete tier; S3 lifecycle backend; metrics; `purged_at` column if explicit audit history needed later.

## Release criteria
/v1 suite passes; full suite passes; engine-freeze guard `git diff v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator` empty; no CAD/frontend/benchmark/schema changes; 410 + row-preservation + dry-run-default + min-age floor + max-delete cap all enforced.
