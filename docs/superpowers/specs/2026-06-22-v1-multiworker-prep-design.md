# Spec — Multi-Worker Preparation (P2)

**Date:** 2026-06-22
**Status:** Approved design → implementation plan
**Baseline:** `v0.2.8-admin-key-rotation`. Engine frozen (`v0.1-benchmark-10of10`).
**Goal:** Build the foundation that makes N worker processes (one host, shared SQLite) safe — atomic single-winner job claiming + lease/heartbeat + lease-scoped recovery — **behind a flag, single worker remaining the default with byte-for-byte current behavior**. Does NOT enable N workers. First (additive, gated) schema change in the P2 line.

## Problem (single-instance assumptions, all in `queue.py`)
- In-memory `asyncio.Queue` holds pending job_ids — per-process, invisible to other instances.
- `recover()` fails **all** `running` jobs on boot — a 2nd instance would mark the 1st's live jobs failed.
- Non-atomic claim (`get_job_row` then `update status=running`) — two workers could grab the same pending job.
- `pending_position` is already DB-based; `CLAUDE_CODE_MAX_CONCURRENT=1` is per-process.

## Locked decisions
- **Foundation behind a flag.** `API_WORKER_MODE` default `"single"` = exact current behavior; `"claim"` = DB-backed claim/lease path.
- **Additive, gated schema change:** idempotent `ALTER TABLE jobs ADD COLUMN` for `claimed_by`, `claimed_at`, `lease_expires_at`. Old rows → NULL, no migration.
- **Lease + heartbeat** claiming; **lease-scoped recovery**.
- **Lease expiry makes a job RECLAIMABLE; it does NOT itself fail the job.** A reclaimed job returns to `pending` (re-runnable); only after `API_WORKER_MAX_RECLAIM` reclaim attempts is it written `failed/internal`.
- Stay **SQLite, single host** (WAL + busy_timeout already set). Cross-host/Postgres = P3.
- Engine frozen; no CAD/frontend/benchmark changes; **multi-worker disabled by default**.

## Schema model (first justified change, additive + gated)
Idempotent in `init_db` (existing `api.db` upgrades on next boot; old rows NULL/0; no data migration):
```sql
ALTER TABLE jobs ADD COLUMN claimed_by TEXT;
ALTER TABLE jobs ADD COLUMN claimed_at TEXT;
ALTER TABLE jobs ADD COLUMN lease_expires_at TEXT;
ALTER TABLE jobs ADD COLUMN reclaim_count INTEGER DEFAULT 0;
```
Each wrapped to swallow the "duplicate column name" error (SQLite has no `ADD COLUMN IF NOT EXISTS`) via a small `_add_column_if_missing(conn, table, coldef)` helper, so re-running `init_db` is safe. `users`/`api_keys`/`user_quota` and the rest of `jobs` unchanged. No new tables.
- `claimed_by` = `worker_id` of the owner.
- `claimed_at` = UTC ISO when claimed.
- `lease_expires_at` = UTC ISO; renewed by heartbeat.
- `reclaim_count` = internal bookkeeping for the reclaim cap (NULL/0 on old rows; incremented each reclaim; used only by recovery). Not exposed in the API.

## Worker identity
`worker_id` derived at process start: `f"{hostname}:{pid}:{uuid4().hex[:8]}"`. Stored in `config` (or computed once in queue). Used as `claimed_by`.

## Claim model (atomic, single-winner)
```sql
UPDATE jobs SET status='running', started_at=?, claimed_by=?, claimed_at=?, lease_expires_at=?
WHERE job_id=? AND status='pending'
```
`cursor.rowcount == 1` → this worker won; `0` → another worker took it (skip, poll next). SQLite serializes writes (WAL + busy_timeout) → exactly one winner. No extra locking.

## Heartbeat
While a claimed job runs, a background task extends `lease_expires_at = now + API_WORKER_LEASE_S` every `API_WORKER_HEARTBEAT_S` (heartbeat ≪ lease). Distinguishes a slow-but-alive long CAD job from a crashed worker. Stops on terminal.

## Recovery model (lease-scoped, NON-failing)
Replaces "fail-all-running" in claim mode. On boot and periodically:
- A `running` job is **reclaimable** iff `lease_expires_at < now` (or NULL for a legacy `running` row at single→claim transition).
- Reclaim = set back to `pending`, clear `claimed_by`/`claimed_at`/`lease_expires_at`, increment `reclaim_count`. **Lease expiry alone never fails the job** — it only makes it reclaimable.
- Only after `reclaim_count >= API_WORKER_MAX_RECLAIM` is the job written `failed/internal` (poison-job guard, so a job that repeatedly crashes its worker can't loop forever).
- A live worker's **unexpired** lease is never touched.

## Queue source (claim mode)
Workers **poll** the DB for the oldest `pending` row (`ORDER BY created_at, job_id LIMIT 1`) every `API_WORKER_POLL_S`, then attempt the atomic claim. `create_job` still inserts a `pending` row; in claim mode the in-memory `enqueue` is a no-op (or a wake nudge). `depth()` → `COUNT(status='pending')` in claim mode. **Single mode keeps the in-memory `asyncio.Queue` path verbatim.**

## Single-worker default (unchanged)
`API_WORKER_MODE="single"` → the current code path exactly (in-memory queue, current `recover()` fail-all). Zero behavioral change; all existing queue/lifecycle tests pass untouched. New logic strictly behind the flag.

## Configuration model (`config.py`)
- `API_WORKER_MODE` (default `"single"`; `"claim"`).
- `API_WORKER_LEASE_S` (default 120), `API_WORKER_HEARTBEAT_S` (default 30), `API_WORKER_POLL_S` (default 2), `API_WORKER_MAX_RECLAIM` (default 3).
- `worker_id` derived at start.

## Concurrency & Claude subscription (operational note)
Claim mode is correct for N processes on one host sharing the SQLite DB. **Caveat (documented, not enforced here):** each worker process spawns its own `claude` child → N workers = N concurrent Claude sessions, multiplying subscription/CPU load. This milestone keeps the mechanism safe but leaves **N=1 (single worker) as default**; actual N-worker rollout + per-host concurrency cap = later milestone. F7 reaper triple-match still reaps orphans per host.

## Interactions
- **F7:** lease-scoped recovery supersedes fail-all in claim mode; reaper unchanged (startup-only, per-host).
- **F11 `_terminal`:** terminal writes unchanged; claim adds the new columns only.
- **Quota/rate-limit/retention:** unaffected (row-based accounting; claim changes *who* runs, not counts; retention operates on terminal jobs/dirs).
- **readyz `queue`:** `alive()` per-process; claim mode also reflects the poll loop running.

## Code areas (all outside frozen `services/`/`orchestrator/`)
- `backend/app/v1/db.py` — additive ALTERs in `init_db`; `claim_job`, `reclaim_expired`, `renew_lease`, `count_pending`, `next_pending` helpers.
- `backend/app/v1/queue.py` — claim-mode worker path (poll → atomic claim → run → heartbeat → terminal), lease recovery; single-mode path verbatim; gated by `API_WORKER_MODE`.
- `backend/app/core/config.py` — worker knobs + `worker_id`.
- `backend/app/main.py` — recovery call selects mode.
- tests.
- **No** engine, CAD, frontend, benchmark changes.

## Testing
- **Atomic claim:** two connections claim one pending row → exactly one `rowcount==1`, other `0`.
- **Lease/recovery:** expired lease → reclaimed to `pending` (NOT failed); unexpired lease → untouched; after `MAX_RECLAIM` reclaims → `failed/internal`. **Assert lease-expiry alone never sets `failed`.**
- **Heartbeat:** `renew_lease` extends `lease_expires_at`; stops on terminal.
- **Mode gate:** `API_WORKER_MODE="single"` → exact current behavior (existing queue tests pass); `"claim"` → DB-claim path.
- **Schema:** idempotent ALTER (double `init_db` no-op); old rows NULL; all four columns present.
- **Regression:** /v1 + full suites green in default (single) mode; engine-freeze guard empty.

## Non-goals
N-worker rollout / supervisor; per-host concurrency cap; Postgres / cross-host; Redis queue; worker registry table; priority queues; graceful drain — all upgrade-path.

## Upgrade path
N-worker rollout + process supervisor honoring the Claude session limit; Postgres + `SELECT FOR UPDATE SKIP LOCKED` (cross-host); Redis queue; worker health/registry table; priority queues; graceful drain on shutdown.

## Release criteria
/v1 suite passes; full suite passes (default single mode); engine-freeze guard `git diff v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator` empty; no CAD/frontend/benchmark changes; `API_WORKER_MODE=single` is byte-for-byte current behavior; claim/lease/recovery correct under `"claim"`; lease expiry makes a job reclaimable, never directly failed.
