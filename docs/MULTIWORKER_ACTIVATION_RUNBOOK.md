# Multi-Worker Activation Runbook

Default is `API_WORKER_MODE=single`, `WORKERS=1` — one in-process worker, the
current committed production behavior. Claim mode (N processes sharing one SQLite
DB) is an **opt-in**, activated via the staged plan below.

**SQLite single-host only.** Do not run workers across hosts — there is no shared
DB across hosts in this topology. Cross-host scaling (Postgres / `SELECT FOR UPDATE
SKIP LOCKED`, Redis) is a future milestone.

## Preconditions
- Foundation present: claim / lease / heartbeat / lease-scoped recovery
  (`v0.2.9-multiworker-prep`) and claim observability (`/v1/admin/claims`).
- **Claude subscription concurrency warning:** effective concurrency =
  `WORKERS × CLAUDE_CODE_MAX_CONCURRENT`. Each worker process spawns its own
  headless `claude` child, so N processes = up to N concurrent Claude sessions.
  Keep `WORKERS × CLAUDE_CODE_MAX_CONCURRENT ≤ the Claude subscription session
  limit`. Start at N=2 and raise one step at a time. There is no cross-process
  global cap — quotas, rate-limits, and the F7 reaper are the existing guards.

## How it launches
`scripts/serve_api.sh` reads `WORKERS` (default 1) → `uvicorn --workers $WORKERS`.
Each forked process runs its own lifespan: startup reaper → recover → claim worker,
with a distinct `WORKER_ID` (`hostname:pid:uuid8`), all sharing the one SQLite DB.

**Boot-reaper caveat (per-host, not per-process):** the F7 orphan-`claude` reaper
runs at each process's startup and skips only its own PID + descendants. It matches
any headless-`claude` process under the workspace root by signature — it cannot tell a
*sibling worker's* live child from a true orphan. During a **simultaneous cold N-boot**,
one process's reaper could therefore kill another freshly-started worker's `claude`
child. The window is the boot instant before any job is claimed (live children are
unlikely to exist yet), and the staged restarts below avoid it. To be safe under load,
prefer a **rolling restart** (start workers a few seconds apart, or rely on the LB to
stage them) over a simultaneous cold start of all N.

## Stage 1 — 1 worker, claim mode
1. `export API_WORKER_MODE=claim WORKERS=1`
2. Restart: `./scripts/serve_api.sh`
3. Submit a job; confirm it reaches `completed`. While it runs, `GET /v1/admin/claims`
   shows it under one `claimed_by`.
**Gate:** claim-mode outcomes match single mode (completed / cancel / timeout). No
`database is locked`.

## Stage 2 — 2 workers
1. `export API_WORKER_MODE=claim WORKERS=2`; restart.
2. Submit several jobs. `GET /v1/admin/claims` → `by_owner` shows 2 distinct
   `claimed_by`; logs on `app.v1.queue` show `claim` / `renew` / `reclaim`.
3. **Duplicate-execution check:** each job must run on exactly one worker. Confirm no
   job_id appears under two `claimed_by`; a `running` job's `claimed_by` never changes.
4. **Stale-lease / reclaim / crash check:** `kill -9` one worker process mid-job. Its
   lease stops renewing and expires (~`API_WORKER_LEASE_S`, default 120s); the
   survivor's recover/poll reclaims it (`reclaim ... count=…` in logs, job → `pending`
   → re-runs). The orphaned `claude` child is killed by the F7 reaper on next boot.
**Gate:** zero duplicate executions; reclaim observed; crash → re-run; no
`database is locked`.

## Stage N — cautious rollout
Raise `WORKERS` one step at a time, always keeping
`WORKERS × CLAUDE_CODE_MAX_CONCURRENT ≤ subscription limit`. After each step confirm:
throughput rises, leases stay renewed (`/v1/admin/claims` not all `stale`), the queue
drains (`pending` returns to 0), no orphaned `claude` processes after a crash.

## Observability — `GET /v1/admin/claims`
Admin-gated. Returns active claims grouped by owner:
```
{ claims:  [ {claimed_by, job_id, status, claimed_at, lease_expires_at, stale} ],
  by_owner:[ {claimed_by, running, oldest_lease} ],
  now: "<utc-iso>" }
```
- `stale = lease_expires_at < now` → a likely-dead owner whose job is about to be
  reclaimed.
- **This is a CLAIM view, not a process census.** An idle worker holding no running
  job does **not** appear; `claimed_by` proves a held claim, not a live process — use
  `stale` to infer a dead owner. A real worker-process registry is a future milestone.
- **Logs** (`app.v1.queue`, INFO): `claim worker=… job=…`, `renew worker=… job=…`,
  `reclaim worker=… count=…`.

## Rollback

### Planned — drain-then-switch (zero lost work; PREFERRED)
1. Stop new submissions (take the node / LB out of rotation; running jobs keep
   finishing).
2. Wait until drained: `pending` count is 0 **and** `GET /v1/admin/claims` →
   `claims: []`.
3. `export API_WORKER_MODE=single WORKERS=1`; restart. Nothing is running, so single
   mode's `recover()` fails nothing. **Zero failures.**

### Emergency — abrupt switch (fast, lossy-but-safe)
1. `export API_WORKER_MODE=single WORKERS=1`; restart immediately.
2. Single mode's `recover()` fails-all-running → in-flight jobs become `failed`
   (re-submittable). No corruption, no stuck rows; claim columns are ignored in single
   mode.

Both rollbacks are config-only (no code or schema revert). The prior single-worker
behavior is byte-for-byte the committed default.

## Success criteria
- Stage 1: claim (1 worker) == single-mode outcomes; suites green.
- Stage 2: 2 workers — zero duplicate executions; renewals observed; crash → reclaim →
  re-run; no `database is locked`.
- Stage N: throughput scales with `WORKERS × CLAUDE_CODE_MAX_CONCURRENT ≤
  subscription limit`; leases stable; queue drains; no orphaned `claude` after crashes.
- `/v1/admin/claims` accurate (active claims; idle workers absent by design).
- Rollback validated: drain-then-switch = zero failures; abrupt = failed /
  re-submittable, no stuck rows.
