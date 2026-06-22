# Spec — Production API `/v1` (Phase 1)

**Date:** 2026-06-22
**Status:** Approved design → implementation plan
**Baseline:** tag `v0.1-benchmark-10of10` (full 10-object benchmark 10/10).
**Goal:** A user submits a CAD generation request through a stable, authenticated `/v1` API while preserving the currently validated 10/10 pipeline behavior.

## Principle
A thin, authenticated, durable **`/v1` facade** that **wraps the existing pipeline services** (`claude_generation`, `job_service`, `event_service`, `artifact_service`, `claude_code_adapter`). The existing `/api/*` stays as the internal compatibility layer (frontend + tests untouched). **No CAD-generation, frontend, or benchmark changes.**

## In scope (Phase 1 / P1 only)
SQLite persistence · in-process job queue (single worker) · Bearer API-key auth · admin key · `/v1` facade endpoints · `healthz`/`readyz`.

## Explicitly excluded
Parallel component generation · caching · frontend rewiring · any CAD pipeline change · latency optimization · per-user quota enforcement & rate limiting (schema may reserve fields; enforcement is P2).

## Architecture
```
single uvicorn process
 ├─ FastAPI /api/*   (existing internal layer — unchanged)
 ├─ FastAPI /v1/*    (NEW facade: auth + queue + SQLite)
 ├─ in-process asyncio FIFO queue, ONE worker (CLAUDE_CODE_MAX_CONCURRENT=1)
 ├─ SQLite storage/api.db (users, api_keys, jobs)   — queryable index
 └─ filesystem storage/projects/{project_id}/        — artifacts/reports (unchanged)
```
A `/v1` **job** maps 1:1 to an internal `project_id` + `job_id`. The queue worker calls the existing `claude_generation.run(project_id, job_id)` unchanged.

## New module layout (`backend/app/v1/`)
| File | Responsibility |
|---|---|
| `db.py` | SQLite connection (WAL) + schema + typed CRUD (users, api_keys, jobs) |
| `auth.py` | key hashing/verify; `require_user` / `require_admin` FastAPI deps; admin key from env |
| `queue.py` | in-process asyncio FIFO + single worker; enqueue/claim/complete; status transitions; startup recovery |
| `models.py` | pydantic request/response schemas |
| `routes.py` | `/v1` endpoints |
| (modify) `app/main.py` | register `/v1` router; start/stop the queue worker via lifespan |
| (new) `scripts/serve_api.sh` | start command (env-driven) |

## Data model (SQLite `storage/api.db`, WAL)
- `users(id TEXT PK, name TEXT, is_admin INT, created_at TEXT)`
- `api_keys(id TEXT PK, user_id TEXT FK, key_hash TEXT, key_prefix TEXT, created_at TEXT, revoked_at TEXT NULL)`
- `jobs(job_id TEXT PK, user_id TEXT FK, project_id TEXT, status TEXT, stage TEXT, failure_class TEXT NULL, created_at, started_at, completed_at, queue_pos INT NULL, metrics_json TEXT NULL)`
- Reserved-for-P2 columns allowed but unused: quota fields. Per-job JSON files remain source of truth; SQLite is the index.

## API contract (`/v1`)
All require `Authorization: Bearer <key>` except `healthz`/`readyz`.

| Method · path | Auth | Behavior |
|---|---|---|
| `GET /v1/me` | user | `{user_id, name, is_admin}` for the authenticated key |
| `POST /v1/jobs` | user | body `{prompt, dimensions?, material?, mode?}` (default mode `qwen_claude_code`). Creates internal project + brief + job, inserts `jobs` row `pending`, enqueues, returns `{job_id, status, queue_pos}` |
| `GET /v1/jobs/{id}` | user (owner) | `{job_id, status, stage, failure_class?, queue_pos?, created/started/completed_at, components_passed?, metrics?}` |
| `GET /v1/jobs/{id}/events` | user (owner) | SSE — reuses `event_service` channel for the job's `project_id`; supports `Last-Event-ID` replay |
| `GET /v1/jobs/{id}/artifacts` | user (owner) | `artifact_service.list_artifacts(project_id)` → `[{name, category, size, sha256, download_url}]` |
| `GET /v1/jobs/{id}/artifacts/{name}` | user (owner) | streamed download, path-safe, owner-scoped |
| `POST /v1/jobs/{id}/cancel` | user (owner) | queued → drop; running → `claude_code_adapter.cancel(job_id)`; sets `cancelled` |
| `POST /v1/admin/keys` | admin | body `{user_name}` → creates user + mints key; returns the **plaintext key once** + `key_prefix` |
| `DELETE /v1/admin/keys/{key_id}` | admin | revoke a key |
| `GET /v1/healthz` | none | liveness `{ok:true}` |
| `GET /v1/readyz` | none | readiness: Claude CLI authed (reuse `claude_code_adapter.health`), Qwen reachable, queue worker alive, `api.db` writable, disk ok → `{ready, checks{...}}` |

Status values: `pending → running → {completed | failed | cancelled}`; `failed` carries `failure_class` ∈ `{cad, quota, turns, internal}` (reuse existing `FAILED_*`).

## Auth
- Key format `sk_<32+ random>`; stored as `sha256(salt+key)` hash + `key_prefix` (first 10 chars) for identification; plaintext returned only at creation.
- `require_user`: parse Bearer → look up active (non-revoked) key → attach `user_id`; 401 otherwise.
- `require_admin`: constant-time compare against env `ADMIN_API_KEY`; 403 otherwise. Admin bootstraps users/keys.
- Ownership: every `/v1/jobs/{id}` resolves the job's `user_id`; mismatch → 404 (not 403, to avoid id enumeration).

## Queue
- In-process `asyncio.Queue` of `job_id`s + a single worker task started in FastAPI lifespan. `POST /v1/jobs` persists `pending` and enqueues. Worker: claim → mark `running` (SQLite + job_service) → `await claude_generation.run(project_id, job_id)` → read terminal job status → persist `completed`/`failed`(+failure_class)/`cancelled` + metrics. Max queue depth (config) → 429.
- **Startup recovery:** on boot, any `running` job in SQLite with no live worker → mark `failed` (`internal`, reason `interrupted_by_restart`); `pending` jobs re-enqueued in `created_at` order. Orphan `claude` children killed via existing `claude_code_adapter.shutdown()` on shutdown.

## Reliability / failure classification
Reuse the pipeline's terminal `job.status` (`COMPLETED`, `FAILED_CAD/QUOTA/TURNS`, `CANCELLED`) → mapped to `/v1` status + `failure_class`. Job-level wall-clock cap (config `JOB_WALLCLOCK_TIMEOUT`, default generous) → cancel + `failed/cad(timeout)`. Per-Claude-call timeout unchanged (900s).

## Observability (P1 minimal)
Structured JSON log per request + per job lifecycle transition (`job_id, user_id, status, failure_class`). `jobs.metrics_json` populated from the existing `component_metrics.json` totals at completion. (Aggregate admin metrics endpoint = P2.)

## Security (P1)
- No unauthenticated generation (every `POST /v1/jobs` needs a user key).
- Input limits: prompt ≤ `CLAUDE_CODE_MAX_PROMPT_CHARS`; dimensions/material validated; `mode` allowlist (`qwen_claude_code` default; `deterministic` allowed).
- Artifact endpoints: owner-scoped + `safe_workspace_path`-style path checks; no directory listing.
- Claude permissions unchanged (component calls `Read,Write,Edit`, sandboxed, no `ANTHROPIC_API_KEY`).
- CORS allowlist via env; admin key + DB path via env only.
- (Token-bucket rate limiting = P2; P1 relies on the single-worker queue + max depth as natural throttle.)

## Deployment
- Single port `8080`, one uvicorn process. Reverse proxy (nginx/Caddy) terminates TLS, disables buffering for `/v1/jobs/*/events` (SSE), enforces body-size.
- Env: `ADMIN_API_KEY` (required), `API_DB_PATH` (default `storage/api.db`), `API_MAX_QUEUE_DEPTH`, `JOB_WALLCLOCK_TIMEOUT`, `V1_CORS_ORIGINS`, plus existing `GENERATION_PROVIDER`, `ORCH_BASE_URL`, `CLAUDE_CODE_*`.
- `scripts/serve_api.sh` start; SIGTERM graceful drain. Qwen served separately.
- Checklist: `ADMIN_API_KEY` set, DB initialized, `readyz` green, proxy SSE config, no `ANTHROPIC_API_KEY` in env, `api.db` backed up.

## Preservation guarantees
- `/api/*` endpoints, `claude_generation`, component/assembly logic, prompts, config defaults, frontend, and benchmark code **unchanged**. `/v1` only *calls* existing services.
- Verification includes running the existing test suite + a `/v1` flow with the Claude pipeline mocked (no real CAD) to prove the facade without touching validated behavior.

## Success criteria (P1)
A holder of a valid API key can `POST /v1/jobs`, poll status / stream events, list+download artifacts, and cancel — durably (survives restart) and authenticated — with the underlying pipeline behavior identical to `v0.1-benchmark-10of10`. Existing tests stay green.
