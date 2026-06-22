# Spec — F5: Per-context SQLite Connections (P2)

**Date:** 2026-06-22
**Status:** Approved design → implementation plan
**Baseline:** `v0.2-production-api-phase1-rc`. Engine frozen (`v0.1-benchmark-10of10`).
**Goal:** Replace the single shared SQLite connection with per-context connections (per-request + per-worker) so the `/v1` API is correct under concurrent request/worker load and ready for P3, without schema, API-contract, CAD, frontend, or benchmark changes.

## Problem
One `sqlite3.Connection` (`check_same_thread=False`, WAL, no `busy_timeout`) is shared via `app.state.db` across: the async queue worker, all sync `/v1` routes (FastAPI threadpool — different threads), and `readyz`. A connection is not thread-safe; `check_same_thread=False` only silences the guard. Risks: interleaved transactions, `cannot start a transaction within a transaction`, lost commits, immediate `database is locked` (no wait). Latent under load; a blocker before P3 multi-worker.

## Fix (approved)
Each execution context opens and owns its own connection; never shared. Keep `db.fn(conn, ...)` CRUD signatures and the SQLite schema unchanged.

- **`connect()`**: add `PRAGMA busy_timeout` (config `API_DB_BUSY_TIMEOUT_MS`, default 5000); keep WAL + FK.
- **`get_conn()` dependency** (in `db.py`): a generator that opens a fresh connection from `config.API_DB_PATH`, `yield`s it, on exception `rollback()`, always `close()` in `finally`. FastAPI caches a dependency per request → `require_user` and the route body share that one request-scoped connection (same threadpool thread), closed at teardown.
- **Per-worker connection**: `JobQueue` opens and owns its own connection (worker lifetime), used only by the single worker task; `recover()` uses a short-lived connection; `stop()` closes the worker connection.
- **`readyz`**: opens a short-lived connection for `SELECT 1`.
- **`app.state.db` shared connection removed.** `get_conn`/`JobQueue` read `config.API_DB_PATH` directly. (`app.state.db_path` optional; not required since both read config.)

## Non-goals
No schema change; no `db.*` CRUD signature change; no `/v1` contract change; no CAD/frontend/benchmark changes; no P3 concurrency raise (worker count stays 1).

## Code areas
- `backend/app/v1/db.py` — `connect()` busy_timeout; new `get_conn()` dependency.
- `backend/app/v1/auth.py` — `require_user`/`require_admin` obtain the request conn via `Depends(db.get_conn)`; `_resolve_user(conn, …)` unchanged.
- `backend/app/v1/routes.py` — every route uses `conn = Depends(db.get_conn)` (drop `_conn`/`app.state.db`); `readyz` short-lived connection.
- `backend/app/v1/queue.py` — `JobQueue(db_path=config.API_DB_PATH)`; worker-owned connection; `recover()` short-lived; `stop()` closes.
- `backend/app/main.py` — lifespan: `init_db` via a short-lived connection; build `JobQueue()` (reads config path); drop the shared `app.state.db` connection.
- `backend/app/core/config.py` — add `API_DB_BUSY_TIMEOUT_MS`.

## Testing
- New concurrent-request test (N parallel job creates/reads) → no `database is locked`, rows consistent.
- Adapt queue/route/auth/admin/integration tests from injecting `app.state.db` (a connection) to setting `API_DB_PATH` (a path) — internal harness only.
- Full suite + /v1 suite green.

## Migration / back-compat
No schema change; existing `storage/api.db` usable as-is. API responses unchanged. Only connection management + test harness plumbing change.

## Release criteria
/v1 suite passes; full suite passes; new concurrent test passes; engine-freeze guard `git diff v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator` empty; no frontend/benchmark/CAD changes.
