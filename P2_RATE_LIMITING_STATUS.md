# P2 — Rate Limiting Status (2026-06-22)

Fifth P2 milestone complete. Per-API-key token-bucket rate limiting at the `/v1` surface via one ASGI middleware, complementing quotas. Engine frozen; CAD pipeline, schema, frontend, benchmark, and `routes.py` unchanged.

## Problem
Quotas cap daily count + concurrent in-flight, but a single key could still hammer read/SSE/admin endpoints or submit-spam within quota, saturating the API process and the single worker. No transient burst protection existed.

## Design (approved decisions)
- **ASGI middleware** enforcement, registered **before** CORS so CORS stays outermost (preflight unaffected). `routes.py` untouched.
- **Token bucket** per `(scope_id, category)`; capacity = per-minute limit, continuous refill `limit/60` tokens/sec, lazy refill on access.
- **Scope:** `hash_key(bearer)` if bearer present (pure sha256, no DB hit), else `ip:<client.host>` for unauthenticated (anti-brute-force). `healthz`/`readyz` exempt.
- **Category is path-based for everyone.** Admin key gets **no bypass** (its `/admin/*` → admin bucket, its `POST /jobs` → submit bucket). Quota admin-bypass is separate, unchanged.
- **Success responses** carry `X-RateLimit-Limit/Remaining/Reset`; **429** carries `Retry-After` + body `{detail, scope:"rate_limit", retry_after}`.
- Single-instance, in-memory; Redis-swappable behind `check()`. No CAD/frontend/benchmark/schema changes.

## Implementation
- `config.py`: `API_RATE_LIMIT_ENABLED` (default 1), `API_RATE_SUBMIT_PER_MIN=10`, `API_RATE_READ_PER_MIN=120`, `API_RATE_SSE_PER_MIN=30`, `API_RATE_ADMIN_PER_MIN=60`, `API_RATE_MAX_BUCKETS=10000`.
- `backend/app/v1/ratelimit.py` (new): `Decision` dataclass, `_buckets` store, `classify(method,path)`, `check(scope_id,category,now=None)`, `_sweep()`, `reset()`, and `RateLimitMiddleware(BaseHTTPMiddleware)`. `_scope_id` reuses `auth._bearer` + `auth.hash_key` (no DB).
- `main.py`: registers `RateLimitMiddleware` before the CORS block (Starlette reverse-registration → CORS outermost). Router includes unchanged.

## Rate limit model
| Category | Endpoints | Default limit |
|---|---|---|
| `submit` | `POST /v1/jobs` | 10/min |
| `read` | `GET /v1/jobs/{id}`, `/artifacts`, `/artifacts/{rel}`, `POST /jobs/{id}/cancel`, `GET /v1/me` | 120/min |
| `sse` | `GET /v1/jobs/{id}/events` (stream creation) | 30/min |
| `admin` | `/v1/admin/*` | 60/min |
| exempt | `/v1/healthz`, `/v1/readyz`, non-`/v1` | — |

Token bucket: `capacity=limit`; `rate=limit/60`/s; `tokens=min(cap, tokens+(now-last)*rate)`; allow if `tokens>=1` then `tokens-=1`. `remaining=floor(tokens)`; `reset=ceil((cap-tokens)/rate)` (0 if full); `retry_after=max(1,ceil((1-tokens)/rate))` when denied.

## Middleware architecture
Single `BaseHTTPMiddleware.dispatch`: disabled flag → passthrough; `classify` path→category (None → passthrough, no headers); resolve `scope_id`; `check()`; denied → 429 JSONResponse with `Retry-After` + `X-RateLimit-*`; allowed → call route, set the three `X-RateLimit-*` on the response. `check()` is fully synchronous; the only `await` (`call_next`) runs after it returns → no interleaving on `_buckets`, no lock needed (single event loop). Registered before CORS → CORS outermost, rate-limit inner, runs before auth/DB. Memory bounded by `_sweep` evicting fully-refilled idle buckets past `API_RATE_MAX_BUCKETS`.

## Headers
- Success + 429: `X-RateLimit-Limit` (capacity), `X-RateLimit-Remaining` (`floor(tokens)`), `X-RateLimit-Reset` (seconds to full).
- 429 only: `Retry-After` (= `retry_after`), body `{ "detail": "rate limit exceeded for <category>", "scope": "rate_limit", "retry_after": N }`.
- Disabled flag → passthrough, no headers. Existing success bodies unchanged.

## Interaction with quotas (no duplication)
Rate limit = transient burst, middleware, in-memory, `scope:"rate_limit"`. Quota = persistent daily/in-flight, in `create_job`, DB-backed, `scope:"in_flight"|"daily"`. `POST /v1/jobs` passes rate limit first, then quota. Distinct, complementary.

## Tests
- `test_v1_ratelimit_unit.py` (8): classify all categories + None; capacity-then-deny; remaining decrement; refill over time; retry_after/reset math; independent scopes+categories; sweep eviction.
- `test_v1_ratelimit_mw.py` (6): submit burst→429 + body + `Retry-After` + `X-RateLimit-*`; success headers; read higher ceiling; healthz exempt (no headers); distinct keys independent; unauthed IP-bucketed; disabled passthrough.
- Regression-safety (env-only): `test_v1_cors.py`, `test_v1_integration.py` set `API_RATE_LIMIT_ENABLED=0` (reuse `main.app`).
- **/v1 suite: 57/57 · full suite: 101/101 · engine-freeze guard empty.**

## Review outcome (opus whole-branch)
**GO. No Critical/High.**
- **Medium M1:** sweep cap is best-effort — under a flood of many distinct active scopes (none fully refilled), the store can transiently exceed `API_RATE_MAX_BUCKETS` until scopes idle. Entries tiny, self-heals; matches single-instance/Redis-upgrade posture. Accepted.
- **Low:** L1 `POST /v1/jobs/` (trailing slash) classifies `read` on first hop but is non-exploitable (Starlette 307 funnels to the canonical `submit` path; non-following clients create no job). L2 mixed-case `/V1/` would skip the limiter but Starlette routing is case-sensitive → 404 before any endpoint.
- **Minors (deferred):** `Decision.reset` docstring wording; sweep test covers only fully-idle case; unused `import math` in unit test; mid-file imports in `ratelimit.py` (documented, avoids `auth→db` circular import).

## Remaining P2 roadmap (not started)
- **Artifact retention** — sweeper for old project dirs (disk pressure).
- **F7** — single-instance recovery; reap stale `claude` children on boot.
- **F10** — admin-key rotation/revocation.
- **F11** — `metrics_json` on failed jobs.
- **F4 follow-up** — monotonic insert-sequence tiebreak for `queue_pos`.
- **Rate-limit upgrade path** — Redis token bucket (multi-instance), trusted-proxy `X-Forwarded-For`, per-key custom rate overrides (mirror `user_quota`), 429/limit metrics, atomic sweep cap.
- **Quota upgrade path** — monthly compute-minutes, per-plan tiers, Redis counters, atomic reservation (TOCTOU).
- **P3** — multi-worker scaling.

## Commits (off `1339d74`)
`bcc22f7` token-bucket core + config knobs · `ba7c891` middleware + main wiring (CORS stays outermost).
Tag: `v0.2.5-rate-limiting`.

*P2 Rate Limiting only. No CAD, frontend, benchmark, schema, routes.py, or engine changes.*
