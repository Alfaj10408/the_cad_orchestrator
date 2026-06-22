# Spec — Quota System + F9 (P2)

**Date:** 2026-06-22
**Status:** Approved design → implementation plan
**Baseline:** `v0.2.3-f6-readyz-hardening`. Engine frozen (`v0.1-benchmark-10of10`).
**Goal:** Per-user quotas (daily job count + concurrent in-flight) enforced at submission, with admin overrides and bypass; plus F9 (refuse the default `API_KEY_SALT` in production).

## Problem
Any valid key can submit unlimited jobs → monopolize the single worker, drain the Claude subscription session limit, and fill disk. No per-user limits exist. Separately (F9), `API_KEY_SALT` defaults to `"dev-salt-change-me"` and isn't enforced in production.

## Locked decisions
- Enforce quotas **only at `POST /v1/jobs`**, **before project creation** (also closes F8 orphan-dir-on-429).
- **Dynamic accounting** from the `jobs` table (no counter table).
- Add **`user_quota` table additively** (`CREATE TABLE IF NOT EXISTS`; no migration).
- **Admin users bypass** quota.
- **Admin override endpoints** to set/clear per-user limits.
- Fold in **F9 `API_KEY_SALT` production fail-fast**.
- No CAD/frontend/benchmark changes; engine-freeze guard empty.

## Quota model
- `daily_job_limit` — jobs created per UTC day per user (default `API_DEFAULT_DAILY_JOB_LIMIT = 50`).
- `max_in_flight` — concurrent `pending`+`running` jobs per user (default `API_DEFAULT_MAX_IN_FLIGHT = 3`).
- `API_QUOTA_ENABLED` (default true) global switch. `is_admin` users bypass. Per-user `user_quota` override supersedes config defaults (NULL column → default).

## Accounting (dynamic)
- `in_flight(user)` = `COUNT(jobs WHERE user_id=? AND status IN ('pending','running'))`.
- `today(user)` = `COUNT(jobs WHERE user_id=? AND created_at >= <UTC-midnight ISO>)`. `created_at` is UTC ISO; lexicographic `>=` is correct (same format/offset).

## Storage
New additive table (in `init_db`):
```sql
user_quota(user_id TEXT PRIMARY KEY, daily_job_limit INTEGER, max_in_flight INTEGER)
```
NULL → config default. `jobs`/`users`/`api_keys` unchanged. Existing `api.db` gains the table on next boot; no data migration. (First P2 item that adds a table — additive only.)

## API contract
- **`POST /v1/jobs`** — quota exceeded → **429**, body `{detail, scope: "in_flight"|"daily", limit, used}`. (Existing queue-full 429 remains a separate backstop.)
- **`GET /v1/me`** — additive `quota` block: `{user_id, name, is_admin, quota:{daily_job_limit, daily_used, max_in_flight, in_flight}}`.
- **`POST /v1/admin/users/{user_id}/quota`** (admin) body `{daily_job_limit?, max_in_flight?}` → upsert override → `{user_id, daily_job_limit, max_in_flight}`.
- **`DELETE /v1/admin/users/{user_id}/quota`** (admin) → clear override → `{cleared: user_id}`.

## Enforcement points
Single point: `create_job`. Order: validate mode/prompt → if `API_QUOTA_ENABLED` and user not admin: `get_quota`; if `in_flight >= max_in_flight` → 429(in_flight); if `today >= daily_job_limit` → 429(daily) — **all before** `ensure_project_skeleton`/brief/`create_job_full`/enqueue. No quota checks on reads/cancels.

## Admin overrides
`require_admin`-gated set/clear of `user_quota`. Admins bypass their own enforcement.

## F9
- `scripts/serve_api.sh`: add `: "${API_KEY_SALT:?set API_KEY_SALT}"`.
- Startup (lifespan): if `API_KEY_SALT == "dev-salt-change-me"` **and** `ADMIN_API_KEY` set → raise (refuse boot). Dev (no admin key) → allow + warn.

## Code areas (all outside frozen `services/`/`orchestrator/`)
- `db.py` — `user_quota` table; `count_in_flight`, `count_created_since`, `get_quota`, `set_quota`, `clear_quota`.
- `routes.py` — quota check in `create_job` (pre-project); `/v1/me` usage; admin quota endpoints.
- `main.py` — F9 startup salt check.
- `config.py` — `API_QUOTA_ENABLED`, `API_DEFAULT_DAILY_JOB_LIMIT=50`, `API_DEFAULT_MAX_IN_FLIGHT=3`.
- `scripts/serve_api.sh` — salt guard.
- tests.

## Non-goals
Monthly compute-minutes; per-plan tiers; Redis counters; rate limiting; retention — all upgrade-path. No `db.*` signature changes beyond the new helpers. No `/v1` job-contract change beyond the additive `/me` block + 429 detail.

## Testing
- db helpers: in_flight/today counts; get_quota default vs override; set/clear.
- create_job: under limit → 201; at in_flight limit → 429(in_flight) and NO project dir created; at daily limit → 429(daily); admin → bypass; `API_QUOTA_ENABLED=False` → no enforcement.
- `/v1/me` → quota block with live usage.
- admin set/clear → reflected in `get_quota` / `/me`.
- F9: default salt + ADMIN_API_KEY set → lifespan raises; non-default salt → boots; dev (no admin key) → boots.
- /v1 + full suites green; engine-freeze guard empty.

## Upgrade path
Monthly compute-minutes soft cap; per-plan tiers (`plan` column); Redis counters for multi-instance; rate-limit integration; `Retry-After` precision.

## Release criteria
/v1 suite passes; full suite passes; engine-freeze guard empty; no CAD/frontend/benchmark changes.
