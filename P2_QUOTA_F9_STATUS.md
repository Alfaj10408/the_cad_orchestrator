# P2 — Quota System + F9 Status (2026-06-22)

Fourth P2 milestone complete. Per-user quotas (daily job count + concurrent in-flight) enforced at submission, with admin overrides and bypass; F8 orphan-dir-on-429 fixed; F9 production salt fail-fast added. Engine frozen; CAD pipeline, schema (jobs/users/api_keys), frontend, and benchmark unchanged.

## Problem
Any valid key could submit unlimited jobs → monopolize the single worker, drain the Claude subscription session limit, and fill disk. No per-user limits existed. Separately (F9), `API_KEY_SALT` defaulted to `"dev-salt-change-me"` and was never enforced in production. (F8) A queue-full 429 left an orphan project dir because the project was created before the depth check.

## Design (approved decisions)
- Enforce quotas **only at `POST /v1/jobs`**, **before project creation** (closes F8).
- **Dynamic accounting** from the `jobs` table — no counter table.
- Add **`user_quota` table additively** (`CREATE TABLE IF NOT EXISTS`; no migration).
- **Admin users bypass** quota entirely (even with an override row).
- **Admin override endpoints** to set/clear per-user limits.
- Fold in **F9 `API_KEY_SALT` production fail-fast**.
- No CAD/frontend/benchmark/schema changes; engine-freeze guard empty.

## Implementation
- `config.py`: `API_QUOTA_ENABLED` (default true), `API_DEFAULT_DAILY_JOB_LIMIT=50`, `API_DEFAULT_MAX_IN_FLIGHT=3`.
- `db.py`: additive `user_quota(user_id PK, daily_job_limit, max_in_flight)`; helpers `count_in_flight`, `count_created_since`, `get_quota` (override merged over config, per-field NULL fallback), `set_quota` (UPSERT `ON CONFLICT`), `clear_quota`. All new SQL uses bound params.
- `routes.py`: quota block in `create_job` after mode/prompt validation, before project skeleton/brief/enqueue → 429 `{detail, scope:"in_flight"|"daily", limit, used}`; rewrote `/v1/me` with additive `quota` block; added admin `POST/DELETE /v1/admin/users/{id}/quota` (`require_admin`).
- `main.py`: F9 guard at top of `_lifespan` — raises `RuntimeError` if `API_KEY_SALT=="dev-salt-change-me"` AND `ADMIN_API_KEY` set; dev (no admin key) boots with warning.
- `scripts/serve_api.sh`: `API_KEY_SALT` presence guard (`:?`).

## Quota model
- `daily_job_limit` — jobs created per UTC day per user (default 50).
- `max_in_flight` — concurrent `pending`+`running` jobs per user (default 3).
- `in_flight(user) = COUNT(jobs WHERE user_id=? AND status IN ('pending','running'))`.
- `today(user) = COUNT(jobs WHERE user_id=? AND created_at >= <UTC-midnight ISO>)` — `created_at` and the midnight bound share format/offset, so lexicographic `>=` is correct.
- `API_QUOTA_ENABLED` global switch; per-user `user_quota` override (NULL column → config default per field).

## Admin model
- `is_admin` users bypass enforcement entirely (short-circuit before `get_quota`).
- `POST /v1/admin/users/{id}/quota` body `{daily_job_limit?, max_in_flight?}` → UPSERT override → `{user_id, daily_job_limit, max_in_flight}`.
- `DELETE /v1/admin/users/{id}/quota` → clear override → `{cleared: id}`.
- Both `require_admin`-gated (non-admin → 403). Admin identity is server-side from DB `is_admin`, never client-supplied.

## F9 behavior
- Production (`ADMIN_API_KEY` set) + default salt → lifespan raises `RuntimeError`, app refuses boot.
- Non-default salt → boots normally.
- Dev (no `ADMIN_API_KEY`) → boots with a warning (default salt tolerated).
- `serve_api.sh` adds a presence guard for `API_KEY_SALT` (defense-in-depth; lifespan catches the default-value case).

## Tests
- `test_v1_quota_db.py` — in_flight/today counts, get_quota default vs override (incl. partial), set/clear.
- `test_v1_quota_enforce.py` — under limit 201; in_flight limit 429 + NO project dir; daily limit 429; admin bypass; `API_QUOTA_ENABLED=False` no enforcement.
- `test_v1_quota_admin.py` — admin set/clear reflected in get_quota/`/me`; non-admin 403.
- `test_v1_f9_salt.py` — default+admin raises; non-default boots; dev boots.
- Test-only adaptations: `test_v1_cors.py` sets a non-default `API_KEY_SALT` in its reload helper (F9 interaction); `test_v1_concurrency.py` disables quota (DB-lock test, not a quota test).
- **/v1 suite: 43/43 · full suite: 87/87.**

## Review outcome (opus whole-branch)
**GO. No Critical/High.** Engine-freeze guard empty. Confirmed: enforcement pre-project (no orphan on 429), correct 429 shape/order, dynamic accounting & lexicographic UTC comparison, admin bypass with override row, per-field NULL fallback, UPSERT correctness, live `/v1/me` usage, F9 three-case behavior, additive-only API changes, additive table (no migration), legitimate test-only adaptations, bound-param SQL, server-side admin gating.
- **Medium (accepted, spec-acknowledged):** TOCTOU — two concurrent same-user submits can both pass the check before either inserts its row, exceeding quota by one. Single in-process worker mitigates execution; the check itself isn't atomic. Acceptable for MVP v1.
- **Low:** `count_in_flight` queried twice on the in_flight-429 path (minor redundancy); `serve_api.sh` salt guard is presence-only (default value still caught by lifespan).

## Remaining P2 roadmap (not started)
- **Rate limiting** — token-bucket per key (paired with quotas; next-highest risk reduction).
- **Artifact retention** — sweeper for old project dirs (disk pressure).
- **F7** — single-instance recovery assumption; reap stale `claude` children on boot.
- **F10** — admin-key rotation/revocation.
- **F11** — `metrics_json` on failed jobs.
- **F4 follow-up** — monotonic insert-sequence tiebreak for `queue_pos`.
- **Quota upgrade path** — monthly compute-minutes soft cap, per-plan tiers (`plan` column), Redis counters for multi-instance, atomic reservation to close the TOCTOU window, `Retry-After` precision.
- **P3** — multi-worker scaling.

## Commits (off plan `87003ff`)
`3110c66` config + user_quota table + helpers · `b524e15` enforce pre-project (+F8) · `ee6ecbf` /v1/me usage + admin quota endpoints · `b04d6d9` F9 salt fail-fast · `490361b` test-harness adaptations.
Tag: `v0.2.4-quota-f9`.

*P2 Quota+F9 only. No CAD, frontend, benchmark, schema, or engine changes.*
