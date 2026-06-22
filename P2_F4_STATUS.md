# P2 — F4 (dynamic queue_pos) Status (2026-06-22)

Second P2 item complete. `queue_pos` is now computed live; staleness eliminated. Engine frozen; `/v1` contract, schema, queue.py, and CAD pipeline unchanged.

## Problem
`queue_pos` was persisted once at `POST /v1/jobs` (the `asyncio.Queue.qsize()` at enqueue) and echoed unchanged on read → stale after another job completes, a job is cancelled, the worker claims a pending job, or restart recovery re-enqueues. Non-pending jobs also reported a non-null position.

## Design (approved decisions)
- `queue_pos` computed **on read** (single source of truth); **persistence removed**; **column left in place** (no schema migration); **non-pending → null**.

## Implementation
- **`db.pending_position(conn, job_id)`** — 1-based rank among `status='pending'` rows by `(created_at, job_id)`; `None` if the job is missing or not pending. One parameterized SELECT.
- **`routes.py`** — `get_job` and the `create_job` response return `db.pending_position(...)`; the `db.update_job(..., queue_pos=…)` write is removed. `JobView.queue_pos: int | None` unchanged; enqueue + 429 path intact.
- **`queue.py`** — unchanged (its `enqueue` return is simply unused now).
- No schema change; `db.*` CRUD signatures unchanged.

## Tests
- `db.pending_position`: ranks 1/2/3 by created_at; non-pending/missing → None; shifts when a job leaves pending.
- Route-level `test_route_queue_pos_is_live`: 3 jobs → 1,2,3; flip first→running → null + others shift; cancelled → null + shift; completed → null.
- `test_recovery_order_preserves_created_at`: `recover()` re-enqueues in created_at order; ranks 1,2,3.
- **/v1 suite: 23/23 · full suite: 67/67.**

## Review outcome (opus whole-branch)
**GO. No Critical/High.** Verified: SQL 1-based + deterministic + pending-only + None for missing/non-pending; cancel/claim/complete shift correctly; recovery order consistent; contract unchanged; non-pending → null; backward-compatible (old stale column values never read; no migration); engine + queue.py frozen.
- **Low (non-blocking):** when two pending jobs share an identical microsecond `created_at`, the `(created_at, job_id)` tiebreak uses the random UUID `job_id`, which may swap that tied pair's *advisory* position vs true insertion order. Ranks stay a valid contiguous permutation; the worker still runs true FIFO. Optional follow-up: tiebreak on a monotonic insert sequence/`rowid`.
- Nit: `queue.enqueue()` return value now dead (harmless; queue.py correctly frozen).

## Remaining P2 work (not started)
- **F4 follow-up (Low):** monotonic insert-sequence tiebreak (only matters under identical-microsecond collisions).
- **F6** `readyz` Qwen/orchestrator + disk checks.
- **F7** single-instance recovery assumption; reap stale `claude` children on boot.
- **F8** project/brief created before queue-full check → orphan dir on 429.
- **F9** enforce `API_KEY_SALT` at startup.
- **F10** admin-key rotation/revocation.
- **F11** `metrics_json` on failed jobs.
- Deferred features: per-user quota enforcement, token-bucket rate limits, retention sweeper, aggregate admin metrics endpoint.

## Commits (F4, off plan `042927e`)
`0fc80d6` db.pending_position · `72524f6` live queue_pos on read + stop persisting · `42f4773` recovery-order test + verification.
Tag: `v0.2.2-f4-dynamic-queue-pos`.

*P2/F4 only. No schema, frontend, benchmark, queue.py, or CAD changes. db CRUD signatures unchanged.*
