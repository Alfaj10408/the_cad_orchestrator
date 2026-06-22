# P2 — F5 (SQLite connection hardening) Status (2026-06-22)

First P2 item complete: the shared SQLite connection is replaced with per-context connections. Engine frozen; `/v1` contract, schema, and CAD pipeline unchanged.

## What changed
- **`db.connect()`**: `PRAGMA busy_timeout` (config `API_DB_BUSY_TIMEOUT_MS`, default 5000); WAL + FK retained.
- **`db.get_conn()`**: per-request FastAPI dependency — open → yield → rollback-on-error → close. FastAPI caches it per request, so `require_user` and the route body share one request-scoped connection (same threadpool thread), closed at teardown.
- **`JobQueue(db_path)`**: worker opens and owns its own connection (worker lifetime); `recover()` and `readyz` use short-lived connections; `stop()` closes the worker conn.
- **Shared `app.state.db` removed** (zero references remain in `backend/` + `tests/`).
- `db.*` CRUD signatures (`fn(conn, …)`) and the schema are **unchanged**.

## Whole-branch review (opus)
**GO. No Critical/High.** Verified: get_conn lifecycle + dep-caching correct; no connection leaks of consequence; concurrency-correct for the single-worker + multi-request model (WAL + busy_timeout, short autocommit writes); worker conn single-threaded; all Phase-1 contracts preserved (auth/ownership-404/queue/SSE/admin); engine-freeze ZERO changes. Nits only: one unused `Request` import in `auth.py`; a negligible theoretical readyz GC case. Neither blocks release.

## Tests
- **Concurrent-writer regression** (`test_v1_concurrency.py`): 20 parallel `POST /v1/jobs` writers + interleaved/mixed reads → zero `database is locked`, zero 500s, all 201/200.
- **/v1 suite: 19/19** · **full suite: 63/63**.
- **Engine-freeze guard:** `git diff v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator` → **empty**.

## Remaining P2 work (not started)
- **F4** `queue_pos` staleness (compute on read or null on `running`).
- **F6** `readyz` add Qwen/orchestrator + disk checks.
- **F7** single-instance recovery assumption; reap stale `claude` children on boot.
- **F8** project/brief created before queue-full check → orphan dir on 429.
- **F9** enforce `API_KEY_SALT` at startup.
- **F10** admin-key rotation/revocation.
- **F11** `metrics_json` on failed jobs.
- Plus P2 features deferred from the RC: per-user quota enforcement, token-bucket rate limits, retention sweeper, aggregate admin metrics endpoint.
- **F5 follow-up before P3:** per-request/per-worker connections are now P3-ready (each future worker gets its own connection).

## Commits (F5, off plan `6fef73b`)
`05cb99f` busy_timeout + get_conn · `b3ba446` require_user via get_conn · `45079a0` routes per-request conn + readyz · `b445c80` JobQueue worker-owned conn · `ab0f8e2` lifespan path-based · `8af8aca` concurrent-writer test.
Tag: `v0.2.1-f5-hardening`.

*P2/F5 only. No schema, frontend, benchmark, or CAD changes. db CRUD signatures unchanged.*
