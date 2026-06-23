# P3 — Multi-Worker Activation Status (2026-06-23)

Tenth milestone complete. Turns the multi-worker foundation (`v0.2.9-multiworker-prep`) into a **validated opt-in**: claim observability (`GET /v1/admin/claims`), structured claim/renew/reclaim logs, a `WORKERS` launch knob, and a staged activation/rollback runbook. **`API_WORKER_MODE=single`, `WORKERS=1` remain the committed defaults.** No schema change. Engine frozen; CAD pipeline, frontend, benchmark unchanged.

## Problem
The prep milestone made N workers *safe* (atomic claim + lease + lease-scoped recovery, flag-gated) but provided no way to **operate** them: no worker/claim visibility, no multi-process launch path, no validated activation/rollback procedure. Single uvicorn process only; claim mode never exercised under real concurrency.

## Design (validated opt-in)
Deliver the activation harness + observability + runbook **without flipping the default**. SQLite single-host validated before any Redis/Postgres. `/v1/admin/claims` = an **active-claims view, not a process census**. Rollback documented as drain-then-switch (planned) + abrupt (emergency). N processes = `uvicorn --workers N` sharing one SQLite DB; effective concurrency bounded by the Claude subscription session limit.

## Implementation
- `backend/app/v1/db.py`: read-only `active_claims(conn)` (SELECT running jobs + claim columns, ORDER BY claimed_at). No DDL.
- `backend/app/v1/routes.py`: `GET /v1/admin/claims` (require_admin) → `{claims, by_owner, now}`, per-claim `stale = lease_expires_at < now`.
- `backend/app/v1/queue.py`: logger `app.v1.queue` + 3 behavior-neutral INFO sites — `claim`, `renew`, `reclaim` (captures reclaim count). No semantic/ownership change.
- `scripts/serve_api.sh`: `WORKERS` env → `uvicorn --workers ${WORKERS:-1}`.
- `docs/MULTIWORKER_ACTIVATION_RUNBOOK.md`: staged activation + rollback + caveats.

## /v1/admin/claims
Admin-gated, read-only, derives from `jobs` columns (no schema change):
```
{ claims:  [ {claimed_by, job_id, status, claimed_at, lease_expires_at, stale} ],
  by_owner:[ {claimed_by, running, oldest_lease} ],
  now: "<utc-iso>" }
```
- `stale = lease_expires_at < now` → a likely-dead owner whose job is about to be reclaimed.
- **Active-claims view, NOT a process census** (documented in code on both the helper and the route): an idle worker holding no running job does **not** appear; `claimed_by` proves a held claim, not a live process. Real worker-process registry deferred.
- Pending jobs excluded; empty → `{claims:[], by_owner:[]}`; non-admin → 403.

## WORKERS launch knob
`serve_api.sh` reads `WORKERS` (default 1) → `uvicorn --workers $WORKERS`. **`WORKERS=1` reproduces today's exact single-process launch.** N>1 forks N processes sharing one SQLite DB, each with a distinct `WORKER_ID` and its own claim worker. Guards (`set -euo pipefail`, admin-key, `API_KEY_SALT:?`) preserved. Effective concurrency = `WORKERS × CLAUDE_CODE_MAX_CONCURRENT` — keep ≤ the Claude subscription session limit.

## Activation runbook
`docs/MULTIWORKER_ACTIVATION_RUNBOOK.md`:
- **Stage 1** — `WORKERS=1`, `API_WORKER_MODE=claim`: claim-path parity with single mode.
- **Stage 2** — `WORKERS=2`: duplicate-execution check (each job one owner), stale-lease/reclaim/crash check (`kill -9` → lease expiry → survivor reclaims → re-run; F7 reaper kills the orphan child).
- **Stage N** — cautious step-up, concurrency ≤ subscription.
- `/v1/admin/claims` usage + `app.v1.queue` log lines.
- **Boot-reaper caveat:** F7 reaper is per-host (skips only its own descendants) → prefer **rolling restart** over simultaneous cold N-boot to avoid killing a sibling's freshly-spawned `claude` child.
- Subscription concurrency warning; claim-view-not-process-census caveat.

## Validation results
- Targeted multi-worker activation: **22/22** (claims API + activation + claim + worker-db).
- /v1 suite: **135/135** · full suite: **179/179** · engine-freeze guard **empty**.
- Concurrency tests assert REAL behavior: two workers / one job → runs exactly once; 6 jobs / two workers → each once, none twice; NULL-lease running → reclaimed→pending + reclaim log.
- Defaults intact: `API_WORKER_MODE=single`, `WORKERS=1`.

## Review outcome (opus whole-branch)
**GO. No Critical/High.** N-process-on-one-SQLite boot **safe by construction** (WAL + busy_timeout serialize writers; atomic single-winner claim → ≤1 owner; idempotent `init_db` + requeue-only `reclaim_expired` survive concurrent boot). `/v1/admin/claims` read-only + correct stale logic; logs additive/behavior-neutral; defaults preserved; no schema/claim/lease/ownership/engine changes.
- **Medium (addressed):** per-host reaper could kill a sibling's live `claude` child on simultaneous cold N-boot → mitigated by the runbook rolling-restart caveat (doc-only).
- **Lows (deferred):** logger import placement (PEP8); NULL-`claimed_by` grouping edge (only via direct DB manipulation).

## Rollback model
- **Planned — drain-then-switch (PREFERRED, zero lost work):** stop new submissions → wait drained (`pending == 0` and `/v1/admin/claims` → `claims: []`) → set `API_WORKER_MODE=single WORKERS=1`, restart. Nothing running → single `recover()` fails nothing.
- **Emergency — abrupt switch (fast, lossy-but-safe):** flip to single/`WORKERS=1`, restart immediately → single `recover()` fails-all-running → in-flight jobs `failed` (re-submittable), no corruption/stuck rows; claim columns ignored.
- Both config-only (no code/schema revert).

## Remaining roadmap (not started)
- **Default flip to claim** — once proven in the target env (trivial env/default change).
- **Real worker-process registry** — `workers(worker_id, host, pid, started_at, last_seen)` heartbeated per process → a true `GET /v1/admin/workers` (idle + active). New table.
- **Cross-process global concurrency cap** — DB/Redis-backed running cap honoring the subscription limit.
- **Cross-host scaling** — Postgres + `SELECT FOR UPDATE SKIP LOCKED`; Redis queue; reverse-proxy N-instance topology; aggregate cross-process counters table.
- **Poison-job handling** — bounded retry / dead-letter (deferred from prep): `reclaim_count` + `API_WORKER_MAX_RECLAIM`.
- **F4 follow-up** — monotonic insert-sequence tiebreak for `queue_pos`.
- Prior upgrade paths: rate-limit/quota/retention Redis backends + per-key/user overrides; F10 hot-reload; F7/F11 periodic reaper + failure time-series.

## Commits (off `cf87e32`)
`73c79c5` admin claims view (T1) · `30d42cb` claim/renew/reclaim logs + no-duplicate-execution validation (T2) · `5d68966` WORKERS knob + activation/rollback runbook (T3).
Tag: `v0.3.0-multiworker-activation`.

*Multi-worker activation — validated opt-in. Default single-worker / WORKERS=1 unchanged. No schema/CAD/frontend/benchmark/engine changes; queue ownership/claim/lease semantics untouched.*
