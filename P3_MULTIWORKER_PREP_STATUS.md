# P3 — Multi-Worker Preparation Status (2026-06-22)

Ninth milestone (P3 foundation) complete. A flag-gated, DB-backed claim/lease/heartbeat foundation makes N worker processes (one host, shared SQLite) safe — **without enabling N workers**. `API_WORKER_MODE="single"` (default) keeps today's exact behavior. First (additive, gated) schema change in the line. Engine frozen; CAD pipeline, frontend, and benchmark unchanged.

## Problem
Single-instance assumptions in `queue.py` blocked multiple workers:
- In-memory `asyncio.Queue` holds pending job_ids — per-process, invisible to other instances.
- `recover()` failed **all** `running` jobs on boot — a 2nd instance would mark the 1st's live jobs failed.
- Non-atomic claim (`get_job_row` then `update status=running`) — two workers could grab the same pending job.

## Design (approved, scope-limited)
Strictly **ownership / leasing / claiming / recovery**, behind a flag. Atomic single-winner job claiming + lease/heartbeat + lease-scoped recovery. Single worker remains the default with byte-for-byte current behavior. Poison-job / bounded-retry / dead-letter / fail-on-expiry explicitly **out of scope** (deferred to roadmap). SQLite single-host (WAL + busy_timeout); cross-host/Postgres = later.

## Schema additions (first justified change, additive + gated)
Three idempotent `ALTER TABLE jobs ADD COLUMN` in `init_db` (via `_add_column_if_missing`, PRAGMA-guarded; old rows NULL; no data migration):
```sql
ALTER TABLE jobs ADD COLUMN claimed_by TEXT;
ALTER TABLE jobs ADD COLUMN claimed_at TEXT;
ALTER TABLE jobs ADD COLUMN lease_expires_at TEXT;
```
Exactly these three. **No `reclaim_count`**, no 4th column, no new tables. `users`/`api_keys`/`user_quota`/rest of `jobs` unchanged.

## Ownership model
`worker_id = f"{hostname}:{pid}:{uuid4().hex[:8]}"` (`config.WORKER_ID`, derived at process start). A running job is owned by the worker in `claimed_by`. "Live ownership" of the current process = jobs it claimed this run; a prior-instance orphan (reparented to init) is owned by a stale `worker_id` and is reclaimable once its lease expires.

## Leasing model
`claimed_at` = UTC ISO at claim; `lease_expires_at` = UTC ISO, set to `now + API_WORKER_LEASE_S` (default 120s) at claim. A background heartbeat (`_heartbeat`) renews `lease_expires_at` every `API_WORKER_HEARTBEAT_S` (default 30s) while the job runs — distinguishing a slow-but-alive long CAD job from a crashed worker. `renew_lease` is owner+running scoped; heartbeat is cancelled on every terminal exit path.

## Claiming model (atomic, single-winner)
```sql
UPDATE jobs SET status='running', started_at=?, claimed_by=?, claimed_at=?, lease_expires_at=?
WHERE job_id=? AND status='pending'
```
`cursor.rowcount == 1` → this worker won; `0` → another worker took it (skip, poll next). SQLite serializes writers (WAL + busy_timeout) → exactly one winner; no extra locking. Verified by a two-connection test (`(True, False)`).

## Recovery model (lease-scoped, requeue-only)
`reclaim_expired(conn, *, now=None) -> int` (claim mode): every `running` job with `lease_expires_at < now` **or** NULL lease →
- `status = pending`
- clear `claimed_by`
- clear `claimed_at`
- clear `lease_expires_at`

Returns count reclaimed. **Lease expiry makes a job reclaimable; it never directly fails the job** — there is no `failed`/`failure_class` write, no cap, no poison-job handling. A live worker's unexpired lease is never touched. Crash-loops are bounded in the meantime by `JOB_WALLCLOCK_TIMEOUT` + the F7 reaper; bounded-retry/dead-letter deferred.

## Claim-mode architecture
`JobQueue.__init__` captures `self.mode = config.API_WORKER_MODE` + `self.worker_id`. All paths branch:
- **`start()`** → `_claim_worker` (claim) or `_worker` (single).
- **`recover()`** → `db.reclaim_expired(conn)` (claim) or fail-all + re-queue pending (single, verbatim).
- **`depth()`** → `db.count_pending` (claim) or `_q.qsize()` (single).
- **`enqueue()`** → `count_pending` backstop, raise if `> API_MAX_QUEUE_DEPTH` (claim); `qsize` check + `put_nowait` (single, verbatim).
- **`_claim_worker`**: poll `next_pending` every `API_WORKER_POLL_S` (default 2s) → atomic `claim_job` (skip if lost) → spawn `_heartbeat` → `wait_for(claude_generation.run, JOB_WALLCLOCK_TIMEOUT)` → cancel heartbeat → terminal write via existing `_terminal`; cancelled-mid-run stays `cancelled`.

`self._conn` is shared by `_claim_worker` and `_heartbeat` on one event loop; every db call is synchronous (no `await` mid-statement) so coroutines interleave only at `await` boundaries — no sqlite race, no lock needed. Single-mode bodies (`_worker`/`_terminal`/`_TERMINAL`/`_load_metrics`) are byte-for-byte unchanged.

## Verification results
- Targeted multi-worker: **14/14** (`test_v1_worker_db` 10, `test_v1_worker_claim` 4).
- /v1 suite: **127/127** · full suite: **171/171** · engine-freeze guard **empty**.
- Explicit checks: `API_WORKER_MODE` default `"single"` ✓; single-mode byte-for-byte ✓; `reclaim_expired` requeue-only ✓; no fail-on-expiry path ✓; no forbidden symbols (`reclaim_count`/`MAX_RECLAIM`/poison/dead-letter) ✓.

## Review outcome (opus whole-branch)
**GO. No Critical/High/Medium.** Prohibited items confirmed absent. Single mode byte-for-byte vs baseline. Atomic single-winner claim, owner-scoped lease renew, requeue-only recovery (NULL-lease treated expired, unexpired untouched), concurrency-safe shared connection. 14 tests assert real behavior. Engine frozen; db.py additive only; no `main.py`/frontend/benchmark changes.
- **Lows (deferred):** `_utc_now()` vs existing `_now()` redundancy; `recover()` self-selects mode inside the queue (cleaner; no `main.py` change) vs the spec's "main.py selects mode" wording — functionally equivalent.

## Remaining roadmap (not started)
- **Poison-job handling** — bounded retry / dead-letter: re-add a `reclaim_count` column + `API_WORKER_MAX_RECLAIM`, fail after N reclaims (deliberately deferred from this milestone).
- **N-worker rollout** — process supervisor / systemd units + per-host concurrency cap honoring the Claude subscription session limit (N workers = N concurrent Claude sessions). The mechanism is now safe; flipping it on is a separate milestone.
- **Cross-host scaling** — Postgres + `SELECT FOR UPDATE SKIP LOCKED`; Redis-backed queue; worker health/registry table; priority queues; graceful drain on shutdown.
- **F4 follow-up** — monotonic insert-sequence tiebreak for `queue_pos`.
- Prior upgrade paths: rate-limit/quota/retention Redis backends + per-key/per-user overrides; F10 hot-reload; F7/F11 periodic reaper + failure time-series.

## Commits (off `f1f129b`)
`9d35266` 3 additive worker columns + atomic claim/lease/reclaim helpers · `e2029d1` config knobs + gated claim-mode worker + heartbeat + lease recovery.
Tag: `v0.2.9-multiworker-prep`.

*Multi-worker preparation only — ownership/leasing/claiming/recovery, flag-gated. Default single-worker unchanged. No poison-job/dead-letter/retry-cap, no N-worker rollout, no CAD/frontend/benchmark/engine changes.*
