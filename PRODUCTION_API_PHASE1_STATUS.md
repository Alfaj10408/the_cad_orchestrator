# Production API — Phase 1 Status (2026-06-22)

/ v1 facade complete and verified. Baseline `v0.1-benchmark-10of10` preserved — the CAD engine is untouched; `/v1` is a pure wrapper.

## Architecture
```
single uvicorn process (scripts/serve_api.sh, port 8080)
 ├─ /api/*   existing internal layer (UNCHANGED — frontend + tests intact)
 ├─ /v1/*    NEW facade: Bearer auth + queue + SQLite
 ├─ in-process asyncio FIFO queue, ONE worker (CLAUDE_CODE_MAX_CONCURRENT=1)
 ├─ SQLite storage/api.db (users, api_keys, jobs)   — queryable index
 └─ filesystem storage/projects/{project_id}/        — artifacts/reports (unchanged)
FastAPI lifespan: open DB → JobQueue.recover()+start() → app.state.{db,queue};
shutdown: queue.stop() → claude_code_adapter.shutdown().
```
A `/v1` job maps 1:1 to an internal `project_id`+`job_id`; the worker calls the existing `claude_generation.run()` unchanged.

## Endpoints
| Method · path | Auth | Purpose |
|---|---|---|
| `GET /v1/healthz` | none | liveness `{ok:true}` |
| `GET /v1/readyz` | none | readiness `{ready, checks{db, claude_code, worker}}` |
| `GET /v1/me` | user | `{user_id, name, is_admin}` |
| `POST /v1/jobs` | user | create job (validates mode allowlist + prompt length); enqueues; `{job_id, status, queue_pos}` |
| `GET /v1/jobs/{id}` | user (owner) | status `{status, stage, failure_class?, queue_pos?, timestamps}` |
| `GET /v1/jobs/{id}/events` | user (owner) | SSE (reuses validated `_gen`; `Last-Event-ID` replay) |
| `GET /v1/jobs/{id}/artifacts` | user (owner) | list artifacts |
| `GET /v1/jobs/{id}/artifacts/{name}` | user (owner) | download (Path.parents containment, no traversal) |
| `POST /v1/jobs/{id}/cancel` | user (owner) | drop queued / kill running |
| `POST /v1/admin/keys` | admin | mint user+key (plaintext returned once) |
| `DELETE /v1/admin/keys/{key_id}` | admin | revoke key |

Cross-user job access → 404 (no id enumeration).

## Auth model
Bearer `sk_...` keys; stored as `sha256(API_KEY_SALT + key)` hash + 10-char prefix (plaintext shown only at mint). `require_user` resolves key→user_id; `require_admin` constant-time (`hmac.compare_digest`) compare vs env `ADMIN_API_KEY` (empty key disables admin). No `ANTHROPIC_API_KEY` involved.

## Queue model
In-process `asyncio.Queue` + single worker (lifespan-managed). `pending → running → {completed|failed|cancelled}`; `failed` carries `failure_class ∈ {cad, quota, turns, internal}` mapped from the pipeline's terminal `job.status`. Max depth `API_MAX_QUEUE_DEPTH` → 429. Per-job wall-clock cap `JOB_WALLCLOCK_TIMEOUT`. Worker is resilient (per-job exceptions caught + persisted; loop continues). **Restart recovery:** orphaned `running` jobs → `failed/internal`; `pending` re-enqueued.

## Persistence model
SQLite `storage/api.db` (WAL) — `users`, `api_keys`, `jobs` (status/stage/failure_class/timestamps/queue_pos/metrics_json). Per-job JSON files remain source of truth; SQLite is the index. Artifacts on filesystem (unchanged), served only via owner-scoped endpoints. `jobs.metrics_json` populated from `component_metrics.json` at completion.

## Tests run
| Suite | Covers | Result |
|---|---|---|
| `test_v1_db.py` | SQLite CRUD, revoked-key exclusion | ✅ |
| `test_v1_auth.py` | key hash/mint, user resolution, admin compare | ✅ |
| `test_v1_queue.py` | worker run→completed, restart recovery | ✅ |
| `test_v1_routes_jobs.py` | auth 401, create+enqueue, status, cross-user 404, **/v1/me** | ✅ |
| `test_v1_health_events.py` | healthz unauth, readyz checks, SSE wiring | ✅ |
| `test_v1_admin.py` | admin-gated mint, minted key works | ✅ |
| `test_v1_integration.py` | end-to-end (lifespan worker, SQLite persist, admin→create→completed) | ✅ |

## Pass/fail summary
- **/v1 suite: 10/10 passed.**
- **Full suite (incl. all existing CAD/benchmark/orchestrator tests): 54/54 passed.**
- **Benchmark-preservation guard:** `git diff v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator` → **empty** (no changes). CAD engine untouched.
- Capability checks (all via passing tests): /v1 auth ✅ · /v1/me ✅ · queue ✅ · SQLite persistence ✅ · admin endpoints ✅ · SSE ✅ · healthz/readyz ✅.

## Remaining work (P2–P4 — not started)
- **P2:** per-user quota enforcement + token-bucket rate limits; retention sweeper; aggregate admin metrics endpoint; richer structured logging.
- **P3:** parallel component generation (raise `CLAUDE_CODE_MAX_CONCURRENT`) — the only future phase touching pipeline orchestration, behind a flag; biggest latency win.
- **P4:** result + component-level caching.
- Separate later: frontend rewire to `/v1`; swap in-process queue for Redis/Celery if multi-worker needed.

## Commits created (this phase, off `v0.1-benchmark-10of10`)
- `499ae0a` spec + plan · `93492cd` add `/v1/me` to spec+plan
- `b488b8f` T1 SQLite db + config · `ff3437d` T2 auth · `1564f3e` T3 queue
- `39e41ef` T4 routes + `/v1/me` · `945ae5d` T4 fix (Path.parents, drop import)
- `5975f58` T5 SSE + healthz/readyz · `06e358d` T6 admin keys
- `ea6f366` T7 lifespan + router + `serve_api.sh`

*Phase 1 verification only. No P2 work. No CAD logic changes. No new CAD benchmarks.*
