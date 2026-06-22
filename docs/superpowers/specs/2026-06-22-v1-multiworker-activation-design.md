# Spec — Multi-Worker Activation (P3)

**Date:** 2026-06-22
**Status:** Approved design → implementation plan
**Baseline:** `v0.2.9-multiworker-prep`. Engine frozen (`v0.1-benchmark-10of10`).
**Goal:** Make `API_WORKER_MODE="claim"` a **validated opt-in** for N worker processes (one host, shared SQLite) — staged activation harness + claim observability + a rollback/activation runbook. **`API_WORKER_MODE="single"` stays the committed default.** No schema change.

## Locked decisions
- **Observability:** `GET /v1/admin/claims` — represents **active claims**, not actual worker processes. Idle workers do not appear (documented). Real worker-process registry deferred to a future milestone.
- **Rollback:** planned = **drain-then-switch** (zero lost work); emergency = **abrupt switch** (running jobs may become `failed`/re-submittable). Both documented in the runbook.
- **Process model:** `WORKERS` env in `serve_api.sh` → `uvicorn --workers N`; N forked processes share one socket + one SQLite DB; each runs its own claim worker with a distinct `WORKER_ID`.
- **Concurrency vs subscription:** effective concurrency = `WORKERS × CLAUDE_CODE_MAX_CONCURRENT`; runbook mandates ≤ Claude subscription session limit (start N=2). No new cross-process global cap (deferred).
- No schema/CAD/frontend/benchmark changes; engine frozen; default stays `single`. SQLite single-host validated before any Redis/Postgres.

## Current state analysis
- **Single (default):** one uvicorn process; in-memory `asyncio.Queue`; `_worker` pops+runs; `recover()` fails-all-running + re-queues pending.
- **Claim (built, gated):** `_claim_worker` polls `next_pending` → atomic `claim_job` (`UPDATE … WHERE status='pending'`, `rowcount==1`) → `_heartbeat` renews lease → run → `_terminal`. `recover()` = `reclaim_expired` (requeue-only, never fails).
- **Ownership:** `WORKER_ID = hostname:pid:uuid8`; job owned by `claimed_by`.
- **Lease:** `claimed_at` + `lease_expires_at = now + LEASE_S` (120s); heartbeat every `HEARTBEAT_S` (30s); owner+running-scoped `renew_lease`.
- **Launch:** `serve_api.sh` runs ONE uvicorn process (no `--workers`). N-worker needs N processes on the shared DB.

## Activation plan (staged, SQLite single-host)
`serve_api.sh` gains `WORKERS` (default 1) → `uvicorn --workers $WORKERS`. Each forked process runs lifespan → its own claim worker, distinct `WORKER_ID`, shared DB.
- **Stage 1 — 1 worker, claim** (`WORKERS=1`, `API_WORKER_MODE=claim`): claim path == single-mode outcomes (job→completed, cancel, timeout). Gate: parity.
- **Stage 2 — 2 workers** (`WORKERS=2`): atomic claim (no duplicate execution), lease renewal under load, crash recovery (kill one mid-job → survivor reclaims). Gate: zero duplicate runs; reclaim observed; no `database is locked`.
- **Stage N — small N** (start N=2, raise cautiously): throughput scales; `N × CLAUDE_CODE_MAX_CONCURRENT ≤ subscription limit`. Gate: no subscription exhaustion; leases stable; queue drains.
Cross-host/Postgres only after N-on-one-host is proven.

## Safety checks
- **Duplicate execution detection:** atomic single-winner claim → ≤1 owner. Validate `claude_generation.run` runs exactly once per job across workers (call counter); a `running` job's `claimed_by` never changes. `/v1/admin/claims` surfaces any anomaly.
- **Stale lease recovery:** `reclaim_expired` (built) — expired/NULL lease → `pending`, claim fields cleared, **never failed**. Validate via forced expiry.
- **Worker crash recovery:** SIGKILL a process → its job's lease stops renewing → expires → survivor reclaims → re-runs; orphaned `claude` child killed by F7 reaper (triple-match, per-host) on next boot.
- **Split-brain prevention:** one host + one SQLite DB = one source of truth; atomic `UPDATE` serializes writers (WAL). **Impossible by construction** in this topology; split-brain only arises cross-host (out of scope).

## Observability — `GET /v1/admin/claims`
`require_admin`, read-only, derives from `jobs` columns (no schema change):
```
{ claims:   [ {claimed_by, job_id, status, claimed_at, lease_expires_at, stale} ],
  by_owner: [ {claimed_by, running, oldest_lease} ],
  now: "<utc-iso>" }
```
- `claims` = jobs currently `running` (one row each); `stale = lease_expires_at < now` (likely-dead owner, about to be reclaimed).
- `by_owner` = claims grouped by `claimed_by` (workers currently holding ≥1 running job).
- **Documented limits:** **active claims, not a process census.** An idle worker holding no running job does NOT appear; a `claimed_by` proves a held claim, not a live process (use `stale` for liveness inference).
- **Renewals/reclaims:** structured INFO logs on `app.v1.queue` (`claim worker_id job_id`, `renew job_id`, `reclaim job_id`) — log-derived, per-process.
- **Future:** real registry `workers(worker_id, host, pid, started_at, last_seen)` heartbeated per process → a true `/v1/admin/workers` (idle + active). Deferred (new table).

## Rollback model (runbook)
Single-mode `recover()` fails-all-running by design (no lease concept) → an abrupt switch marks live claim jobs `failed`. This is lossy-but-safe (no corruption, no stuck rows, lease columns ignored), and avoidable.
- **Planned → DRAIN-THEN-SWITCH (default, zero lost work):**
  1. Stop new submissions (take node/LB out of rotation; existing workers keep finishing).
  2. Wait drained: `count_pending == 0` AND no running claims (`/v1/admin/claims` → `claims: []`).
  3. Flip `API_WORKER_MODE=single`, `WORKERS=1`, restart → nothing running → single `recover()` fails nothing. **Zero failures.**
- **Emergency → ABRUPT SWITCH (fast, lossy-but-safe):**
  1. Flip to `single`/`WORKERS=1`, restart immediately.
  2. Single `recover()` fails-all-running → in-flight jobs `failed` (re-submittable). No corruption.
Both config-only (no code/schema revert).

## Code areas (all outside frozen `services/`/`orchestrator/`)
- `scripts/serve_api.sh` — `WORKERS` env → `uvicorn --workers $WORKERS` (default 1).
- `backend/app/v1/routes.py` — `GET /v1/admin/claims` (admin-gated, read-only).
- `backend/app/v1/db.py` — read-only `active_claims(conn, now)` helper (SELECT only, no DDL).
- `backend/app/v1/queue.py` — structured INFO logs for claim/renew/reclaim (no behavior change).
- `docs/` — activation + rollback runbook (`MULTIWORKER_ACTIVATION_RUNBOOK.md`).
- tests.
- **No** schema, CAD, frontend, benchmark changes; engine frozen.

## Concurrency vs subscription (operational note)
Effective concurrency = `WORKERS × CLAUDE_CODE_MAX_CONCURRENT`. No new cross-process global cap (SQLite can't cheaply gate a global semaphore); runbook mandates keeping the product ≤ the subscription limit (start N=2). Existing guards: quotas, rate-limits, F7 reaper. DB/Redis global running-cap = future.

## Testing
- **Deterministic (Stage 1):** claim-mode single-worker parity (job→completed, cancel-mid-run stays cancelled, timeout→failed) via monkeypatched `claude_generation.run`.
- **Concurrency (Stage 2):** two `JobQueue` instances/connections, one pending job → exactly one claims+runs (no duplicate); racing on M jobs → each runs once, all drain.
- **Fault injection:** forced lease expiry → reclaim→pending→re-run; cancelled heartbeat → expiry → reclaim; stale-`claimed_by`+expired-lease at boot → `recover()` reclaims; NULL-lease running → reclaimed.
- **Observability:** `/v1/admin/claims` returns running claims grouped by owner + `stale` flag; non-admin 403; empty → `claims:[]`/`by_owner:[]`.
- **Regression:** /v1 + full suites green in default single mode; engine-freeze guard empty.

## Success criteria
- Stage 1: claim (1 worker) == single outcomes; suites green.
- Stage 2: 2 workers, zero duplicate executions, renewals observed, crash→reclaim→re-run, no `database is locked`.
- Stage N: throughput scales; effective concurrency ≤ subscription; leases stable; queue drains; no orphaned `claude` after crashes.
- `/v1/admin/claims` accurate (active claims; idle workers absent, documented).
- Rollback validated: drain-then-switch (zero failures) and abrupt (failed/re-submittable, no stuck rows).
- Engine-freeze guard empty; `API_WORKER_MODE=single` remains committed default.

## Non-goals
Default flip to claim; real worker-process registry / `/v1/admin/workers`; cross-process global concurrency cap; Postgres / Redis / cross-host; reverse-proxy N-instance topology; aggregate cross-process counters table; poison-job/dead-letter — all future.

## Upgrade path
Worker-process registry + true `/v1/admin/workers`; DB/Redis global running-cap honoring subscription; Postgres + `SELECT FOR UPDATE SKIP LOCKED` (cross-host); Redis queue; aggregate counters; reverse-proxy multi-instance; default flip once proven in target env.

## Release criteria
/v1 suite passes; full suite passes (default single mode); engine-freeze guard `git diff v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator` empty; no CAD/frontend/benchmark/schema changes; `/v1/admin/claims` + `WORKERS` knob + structured logs + runbook delivered; `API_WORKER_MODE=single` remains default.
