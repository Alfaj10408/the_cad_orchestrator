# Spec — Rate Limiting (P2)

**Date:** 2026-06-22
**Status:** Approved design → implementation plan
**Baseline:** `v0.2.4-quota-f9`. Engine frozen (`v0.1-benchmark-10of10`).
**Goal:** Per-API-key (token-bucket) rate limiting at the `/v1` surface to protect against bursts/abuse, complementing — not duplicating — quotas. Single-instance, Redis-upgradeable.

## Problem
Quotas cap daily count + concurrent in-flight, but a single key can still hammer read/SSE/admin endpoints or submit-spam within quota, saturating the API process and the single worker. No transient burst protection exists.

## Locked decisions
- **ASGI middleware** enforcement (registered after CORS, before routers). `routes.py` untouched.
- **Token bucket** per `(scope_id, category)`; capacity = per-minute limit, continuous refill `limit/60` tokens/sec.
- **Scope:** `hash_key(bearer)` if bearer present (pure sha256, no DB hit), else `ip:<client.host>` for unauthenticated (anti-brute-force). `healthz`/`readyz` exempt.
- **Category is path-based for everyone.** Admin key gets **no bypass** (its `/admin/*` → admin bucket, its `POST /jobs` → submit bucket). Quota admin-bypass is separate and unchanged.
- **Success responses** carry `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`. **429** carries `Retry-After` + body `{detail, scope:"rate_limit", retry_after}`.
- No CAD/frontend/benchmark/schema changes; engine-freeze guard empty.

## Rate-limit model
| Category | Endpoints | Default limit |
|---|---|---|
| `submit` | `POST /v1/jobs` | 10/min |
| `read` | `GET /v1/jobs/{id}`, `/jobs/{id}/artifacts`, `/jobs/{id}/artifacts/{rel}`, `POST /jobs/{id}/cancel`, `GET /v1/me` | 120/min |
| `sse` | `GET /v1/jobs/{id}/events` | 30/min |
| `admin` | `/v1/admin/*` | 60/min |
| exempt | `/v1/healthz`, `/v1/readyz` | — (no bucket) |

Token bucket: `capacity = limit`; refill `rate = limit/60` tokens/sec; lazy refill on access (`tokens = min(capacity, tokens + (now-last)*rate)`); allow if `tokens >= 1` then `tokens -= 1`. A request matching no `/v1/*` category (e.g. legacy `/api/*`, viewer, static) is **not** rate-limited by this middleware (out of scope).

## Scope resolution (no DB)
- Bearer present → `scope_id = "key:" + hash_key(bearer)` (`hash_key` = `sha256(API_KEY_SALT + key)`, already in `auth.py`; pure, no DB lookup). Validity is checked later by `require_user`; the bucket keys on the hash regardless.
- No/empty bearer → `scope_id = "ip:" + (request.client.host or "unknown")`.
- (Upgrade path: honor `X-Forwarded-For` when a trusted proxy is configured. Not in MVP — uses `request.client.host`.)

## Storage model
New `backend/app/v1/ratelimit.py`: module-level `_buckets: dict[tuple[str,str], list[float]]` → `[tokens, last_refill_monotonic]`. Accessed **only** from the async middleware → single event-loop thread, read-modify-write between `await`s is atomic, **no lock needed**. Memory bounded by opportunistic sweep: when `len(_buckets)` exceeds `API_RATE_MAX_BUCKETS` (default 10000), drop entries that are fully refilled (`tokens >= capacity`, i.e. idle). Interface (`check(scope_id, category) -> Decision`) is Redis-swappable later. `reset()` helper for tests.

`Decision` = `{allowed: bool, limit: int, remaining: int, reset: int, retry_after: int}` where `remaining = floor(tokens)` after the (attempted) take, `reset` = seconds until the bucket is full again (`ceil((capacity - tokens)/rate)`, 0 if full), `retry_after = max(1, ceil((1 - tokens)/rate))` when denied.

## Enforcement point
Single `@app.middleware("http")` (or `BaseHTTPMiddleware`) in `main.py`, **after** CORS, **before** routers. Flow:
1. If `not API_RATE_LIMIT_ENABLED` → passthrough.
2. Classify `request.url.path` → category (table above). No match or exempt → passthrough.
3. Resolve `scope_id`.
4. `d = ratelimit.check(scope_id, category)`.
5. If `not d.allowed` → return `JSONResponse(429, {detail, scope:"rate_limit", retry_after:d.retry_after})` with `Retry-After: d.retry_after` and the three `X-RateLimit-*` headers.
6. Else call route; on the response set `X-RateLimit-Limit/Remaining/Reset`.

Cheaper than auth/DB, so it runs first. SSE category counts on **stream creation** (the GET), not per event.

## Interaction with quotas (no duplication)
- **Rate limit** — transient burst, middleware, in-memory, `scope:"rate_limit"`, `Retry-After`.
- **Quota** — persistent daily/in-flight, inside `create_job`, DB-backed, `scope:"in_flight"|"daily"`.
- `POST /v1/jobs` passes rate limit (middleware) first, then quota (route). Distinct, complementary 429 scopes.

## Retry-After / headers semantics
- `Retry-After` (429 only): `max(1, ceil((1 - tokens)/rate))` integer seconds.
- `X-RateLimit-Limit`: category capacity (int).
- `X-RateLimit-Remaining`: `floor(tokens)` after the take (int, ≥0).
- `X-RateLimit-Reset`: seconds until bucket is full (int). Present on both success and 429.

## API responses
- Existing success bodies **unchanged**; three `X-RateLimit-*` headers added.
- 429 body: `{ "detail": "rate limit exceeded for <category>", "scope": "rate_limit", "retry_after": N }` + `Retry-After: N` + `X-RateLimit-*`.
- No change to existing 401/403/404/quota-429 paths.

## Config knobs (`config.py`)
`API_RATE_LIMIT_ENABLED` (default 1), `API_RATE_SUBMIT_PER_MIN=10`, `API_RATE_READ_PER_MIN=120`, `API_RATE_SSE_PER_MIN=30`, `API_RATE_ADMIN_PER_MIN=60`, `API_RATE_MAX_BUCKETS=10000`. Disabled → middleware passthrough (no headers).

## Code areas (all outside frozen `services/`/`orchestrator/`)
- `backend/app/v1/ratelimit.py` — new: bucket store, `check()`, `reset()`, category classifier.
- `backend/app/main.py` — register the middleware after CORS.
- `backend/app/core/config.py` — six knobs.
- tests.
- **No** `routes.py`, schema, frontend, benchmark, queue.py, or engine changes.

## Testing
- Bucket/`check` unit: allow up to capacity then deny; lazy refill restores over (monotonic-mocked) time; `remaining`/`reset`/`retry_after` math; sweep evicts idle buckets past `API_RATE_MAX_BUCKETS`.
- Middleware (via `TestClient` on an app with the middleware): 11th `submit` in a window → 429 + `Retry-After` + body shape + `X-RateLimit-*`; success carries the three headers; `read` ceiling higher; `sse` category; `admin` category; `healthz`/`readyz` exempt (no 429, no headers required); unauthed bucketed by IP; two distinct keys independent; `API_RATE_LIMIT_ENABLED=0` → passthrough, no headers.
- Regression: existing tests that build their own `FastAPI()` + `include_router` are unaffected (middleware lives in `main.py`); tests that use `main.app` (`test_v1_cors.py`, `test_v1_integration.py`) keep limits non-tripping (disable or high via env in their fixtures).
- /v1 + full suites green; engine-freeze guard empty.

## Non-goals
Redis/distributed buckets; `X-Forwarded-For` proxy trust; per-key custom rate overrides; sliding-window; 429 metrics endpoint — all upgrade-path. No quota changes. No `/api/*`/viewer/static limiting.

## Upgrade path
Redis token bucket (Lua/sorted-set) behind the same `ratelimit.check` interface for multi-instance; trusted-proxy `X-Forwarded-For`; per-key overrides (mirror `user_quota`); aggregate 429/limit metrics.

## Release criteria
/v1 suite passes; full suite passes; engine-freeze guard `git diff v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator` empty; no CAD/frontend/benchmark/schema changes; success responses carry `X-RateLimit-*`, 429 carries `Retry-After` + locked body shape.
