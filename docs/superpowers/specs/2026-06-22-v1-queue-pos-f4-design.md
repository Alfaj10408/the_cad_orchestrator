# Spec — F4: Dynamic queue_pos (P2)

**Date:** 2026-06-22
**Status:** Approved design → implementation plan
**Baseline:** `v0.2.1-f5-hardening`. Engine frozen (`v0.1-benchmark-10of10`).
**Goal:** Report a job's queue position **live**, computed from the pending set, instead of a stale value persisted once at creation.

## Problem
`POST /v1/jobs` persists `queue_pos` once (the `asyncio.Queue.qsize()` at enqueue); `GET /v1/jobs/{id}` echoes the stored value. It is never recomputed, so it goes stale when another job completes, a job is cancelled, the worker claims a pending job, or restart recovery re-enqueues. Non-pending jobs also keep a non-null `queue_pos`.

## Decisions (approved)
1. **Stop persisting** `queue_pos` (no writes to the column).
2. **Leave the `queue_pos` column** in `jobs` (no schema migration).
3. `queue_pos` is **computed on read**.
4. Dynamic position is the **single source of truth**.
5. **Non-pending job → `queue_pos = null`.**

## Fix
Add `db.pending_position(conn, job_id) -> int | None`: 1-based rank of the job among `status='pending'` rows ordered by `(created_at, job_id)`; `None` if the job is not pending. `GET /v1/jobs/{id}` and the `POST /v1/jobs` response compute `queue_pos` via this helper. `create_job` no longer writes `queue_pos`.

**Correctness:** pending-by-`created_at` order equals the asyncio FIFO order (jobs enqueue in creation order; `recover()` re-enqueues pending in `created_at` order; the worker skips entries no longer `pending`). A job's pending rank == its true position. Cancel/complete/claim shift everyone automatically (they leave the pending set). Read-time computation runs under the F5 per-request connection — no cross-row writes, no races.

## Code areas
- `backend/app/v1/db.py` — add `pending_position`. No schema change; CRUD signatures unchanged.
- `backend/app/v1/routes.py` — `get_job` + `create_job` response use `pending_position`; drop `db.update_job(..., queue_pos=pos)`.
- `backend/app/v1/queue.py` — **unchanged** (`enqueue` keeps its depth guard; its return value is simply no longer used for persistence).
- Tests only otherwise.

## Non-goals
No schema change; no `db.*` CRUD signature change; no `/v1` contract change (response field `queue_pos` stays, now live); no CAD/frontend/benchmark changes; no queue.py behavior change.

## Testing
- Multiple queued jobs → positions `1,2,3,…` by `created_at`.
- Cancel the first queued → remaining shift down.
- Worker claims first (→ running) → it reports `null`; remaining shift down.
- Restart recovery → positions reflect `created_at` order after re-enqueue.
- Non-pending (completed/failed/cancelled) → `null`.
- Full + /v1 suites green.

## Migration / back-compat
No schema change; existing `storage/api.db` usable. `queue_pos` column remains (now always `NULL` for new jobs; any old non-null values are ignored on read). API response field unchanged (now accurate).

## Release criteria
/v1 suite passes; full suite passes; new dynamic-position tests pass; engine-freeze guard `git diff v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator` empty; no CAD/frontend/benchmark/schema changes.
