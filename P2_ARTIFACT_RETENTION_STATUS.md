# P2 — Artifact Retention Status (2026-06-22)

Sixth P2 milestone complete. Automatic disk lifecycle management — expire artifact directories of old terminal jobs (and orphan dirs) under `PROJECTS_ROOT` via startup + periodic + admin sweeps, with dry-run default, hard min-age floor, per-sweep delete cap, 410-Gone API semantics, and metrics logging. Engine frozen; CAD pipeline, schema, frontend, and benchmark unchanged.

## Problem
Generated artifacts (STEP/STL/GLB/reports/logs/project dirs) accumulated indefinitely under `PROJECTS_ROOT`, eventually tripping the F6 `disk` readiness check. No cleanup existed.

## Design (approved decisions)
- **DB `jobs` table is source of truth** (`status` + `completed_at`); filesystem `project_dir` trees owned/deleted by retention. **Job rows preserved** (history + quota accounting intact).
- **Per-status windows**, separate env knobs: completed 7d, failed 3d, cancelled 1d. `pending`/`running` never eligible.
- **Hard min-age floor** `API_RETENTION_MIN_AGE_S` (3600s): nothing younger deleted, even with admin override.
- **Per-sweep delete cap** `API_RETENTION_MAX_DELETE` (1000): bounds blast radius / IO; sets `capped` flag.
- **Schema-free 410 inference:** terminal job + dir absent → `410 {detail:"artifacts expired", purged:true}`. Unknown/cross-user → 404. Live dir → 200.
- **Triggers:** startup + periodic background task + admin endpoint; admin `dry_run=true` by default.
- No CAD/frontend/benchmark/schema changes; engine-freeze empty. `routes.py` changes allowed (API facade, outside frozen dirs).

## Implementation
- `config.py`: `API_RETENTION_ENABLED` (1), `API_RETENTION_COMPLETED_DAYS=7`, `API_RETENTION_FAILED_DAYS=3`, `API_RETENTION_CANCELLED_DAYS=1`, `API_RETENTION_MIN_AGE_S=3600`, `API_RETENTION_SWEEP_INTERVAL_S=3600`, `API_RETENTION_MAX_DELETE=1000`.
- `backend/app/v1/retention.py` (new): `SweepStats` (incl. `duration_ms`), `sweep(conn, *, dry_run=True, overrides=None, now=None)`, `_window_days`, `_completed_epoch`, `_dir_size`, `_safe_under_root` (direct-child containment guard), orphan scan, INFO metrics log.
- `backend/app/v1/models.py`: `JobView.artifacts_available: bool | None` (additive).
- `backend/app/v1/routes.py`: `_purged(r)` helper; 410 on `list_artifacts` + `download_artifact`; `artifacts_available` on `get_job`; `POST /v1/admin/retention/sweep`.
- `backend/app/main.py`: lifespan startup sweep + gated periodic asyncio task (cancelled on shutdown), behind `API_RETENTION_ENABLED`.

## Retention model
Age = `now − completed_at`. Eligible iff `status ∈ {completed,failed,cancelled}` AND `age ≥ max(window(status), MIN_AGE_S)` AND dir exists. Orphan: a direct child of `PROJECTS_ROOT` referenced by **no** job (pending/running protected) with dir mtime ≥ `MIN_AGE_S`. `api.db*` and `PROJECTS_ROOT` itself never targeted.

| status | knob | default |
|---|---|---|
| completed | `API_RETENTION_COMPLETED_DAYS` | 7d |
| failed | `API_RETENTION_FAILED_DAYS` | 3d |
| cancelled | `API_RETENTION_CANCELLED_DAYS` | 1d |
| pending/running | — | never |

## Sweep architecture
Single `sweep(conn, *, dry_run, overrides, now) -> SweepStats` shared by all triggers. Collects targets (terminal-past-window + orphans), then per target: containment guard → size accounting → `shutil.rmtree` (skipped in dry-run) → tally `by_status`. Stops at `MAX_DELETE` (`capped=true`). Dry-run computes `eligible`/`reclaimed_bytes`/`by_status` then forces `deleted=0`, deletes nothing. **Job rows never touched** — directory deletion only.
- **Startup:** one `sweep(dry_run=False)` in lifespan after queue start, gated, exceptions swallowed (bad sweep can't crash boot).
- **Periodic:** background asyncio task every `API_RETENTION_SWEEP_INTERVAL_S`, gated, cancelled on shutdown, exceptions swallowed.
- **Admin:** `POST /v1/admin/retention/sweep`.

## Admin controls
- `POST /v1/admin/retention/sweep` (`require_admin`, 403 non-admin), body `{dry_run?=true, overrides?}`. `dry_run` defaults **true** (preview). `overrides` set per-status day windows for the call, still clamped by `MIN_AGE_S`. Response = `{enabled, dry_run, scanned, eligible, deleted, reclaimed_bytes, capped, by_status, duration_ms}`. `API_RETENTION_ENABLED=0` → `{enabled:false, ...zeroes}` without scanning.
- Under existing `/v1/admin/*` rate-limit `admin` category (60/min).

## 410 behavior
`GET /v1/jobs/{id}/artifacts` and `/artifacts/{rel}`: owned terminal job + project_dir absent → **410** `{detail:"artifacts expired", purged:true}`. Unknown job / cross-user → **404** (via `_owned_row`, ordered before the 410 check). Live dir → unchanged 200. `GET /v1/jobs/{id}` stays 200 with terminal status; additive `artifacts_available: bool` reflects dir presence. No schema change.

## Metrics logging
`sweep()` emits one `logging.INFO` line on logger `app.v1.retention` after every sweep: `scanned`, `eligible`, `deleted`, `reclaimed_bytes`, `duration_ms` (+ `dry_run`). `duration_ms` also on `SweepStats` and in the admin response. Covers all three triggers (log lives inside `sweep`).

## Interaction
- **Quotas:** rows persist → dynamic accounting (daily/in-flight) unaffected; retention frees disk only.
- **Rate limiting:** admin sweep counted under `admin` bucket; no limiter change.
- **F6 readiness `disk`:** retention lowers `STORAGE_ROOT` usage → helps `readyz.disk` stay green. Complementary, no coupling.

## Tests
- `test_v1_retention_unit.py` (9): per-status windows under/over; active never eligible; min-age floor blocks override=0; orphan-by-mtime; dry-run reports/deletes-nothing; MAX_DELETE cap + `capped`; row preservation; metrics log + `duration_ms`.
- `test_v1_retention_api.py` (8): 410 on purged (list+download); 200 live; 404 unknown; `artifacts_available` true/false; admin dry-run default (deleted=0, `duration_ms` present); admin real delete; non-admin 403.
- `test_v1_retention_lifecycle.py` (2): startup sweep called (`dry_run=False`) when enabled; not called when disabled.
- Regression-safety (env-only): `test_v1_cors.py`, `test_v1_integration.py` set `API_RETENTION_ENABLED=0`; retention-api fixture pins `API_RETENTION_ENABLED=True` (suite-order isolation, commit 9727740).
- **/v1 suite: 76/76 · full suite: 120/120 · engine-freeze guard empty.**

## Review outcome (opus whole-branch)
**GO. No Critical/High.**
- **Medium:** `test_v1_cors.py` leaks `API_RETENTION_ENABLED=0` via raw `os.environ`+reload — test-only, zero production impact, neutralized by the fixture pin (9727740). Recommended follow-up: switch that test to `monkeypatch.setenv`.
- **Low/Minors (deferred):** weak `duration_ms>=0` assert; `_safe_under_root` resolves root per-call; `scanned` recounts iterdir (count-only TOCTOU); unused `asyncio` import in lifecycle test; startup `rc.close()` skipped if sweep raises (GC closes it).
- One suite-order regression caught at T4 (cors flips the global flag → later admin-sweep test hit the disabled branch); root-caused, fixed test-only.

## Remaining P2 roadmap (not started)
- **F7** — single-instance recovery; reap stale `claude` children on boot.
- **F10** — admin-key rotation/revocation.
- **F11** — `metrics_json` on failed jobs.
- **F4 follow-up** — monotonic insert-sequence tiebreak for `queue_pos`.
- **Retention upgrade path** — multi-instance sweep coordination (DB `retention_lock`); per-user retention overrides (mirror `user_quota`); soft-delete/trash tier; object-store lifecycle; retention metrics endpoint; `purged_at` audit column if explicit history needed; `monkeypatch.setenv` cleanup in cors test.
- **Rate-limit upgrade** — Redis buckets, trusted-proxy XFF, per-key overrides, 429 metrics.
- **Quota upgrade** — monthly compute-minutes, per-plan tiers, Redis counters, atomic reservation (TOCTOU).
- **P3** — multi-worker scaling.

## Commits (off `3bdb656`)
`9632fbd` sweep core + config knobs · `3d68f76` 410 + artifacts_available + admin sweep · `5884b13` startup + periodic lifecycle wiring · `9727740` test isolation fix.
Tag: `v0.2.6-artifact-retention`.

*P2 Artifact Retention only. No CAD, frontend, benchmark, schema, or engine changes.*
