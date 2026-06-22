# Spec — F10 Admin Key Rotation (P2)

**Date:** 2026-06-22
**Status:** Approved design → implementation plan
**Baseline:** `v0.2.7-f7-f11-hardening`. Engine frozen (`v0.1-benchmark-10of10`).
**Goal:** Multiple concurrent admin keys via `ADMIN_API_KEYS` for zero-downtime rotation (overlap window) + revocation, preserving existing single-key admin behavior. No schema change.

## Problem
`config.ADMIN_API_KEY` is a single env string; `auth._is_admin` does one `compare_digest`. Replacing it is a hard cutover that breaks every in-flight admin client; no overlap, no per-key revoke.

## Locked decisions
- **Union model:** effective admin set = `ADMIN_API_KEY` (legacy single) ∪ `ADMIN_API_KEYS` (comma list), deduped, empties dropped. Backward-compatible.
- **Restart-based + overlap** rotation (read at process start; rolling restart + overlap window = zero downtime). No hot-reload.
- **Diagnostics endpoint** `GET /v1/admin/keys/info`, gated by `ADMIN_KEYS_INFO_ENABLED` (default true).
- **F9 compatibility:** boot guard refuses default salt when the effective admin set is non-empty (covers both env vars).
- **No schema change**; keys stay in env (never DB/logs); fingerprint is non-reversible.
- Fingerprint format: `sha256(key.encode()).hexdigest()[:8]`.
- Engine frozen; no CAD/frontend/benchmark changes.

## Current behavior analysis
- `config.ADMIN_API_KEY` default `""`. `auth._is_admin(authorization)` → `bool(admin) and token and hmac.compare_digest(token, admin)`. `require_admin` gates `/v1/admin/*` (403 else). F9 guard (`main.py:30`) checks `config.ADMIN_API_KEY`. `serve_api.sh` requires `ADMIN_API_KEY`. 8 test files set it.

## Threat model
- **Leaked key:** drop from the set + rolling restart; other keys keep working — per-key blast radius.
- **Timing oracle:** set check compares **all** keys with `compare_digest`, ORs results (no early-return) → leaks only key *count*, not *which*.
- **Key exposure:** env-only; diagnostics expose `sha256(key)[:8]` (non-reversible), never key material.
- **Stale credential:** revoke is immediate on the restart dropping the key (env = single source of truth).
- **Downgrade:** union guarantees a misconfig can't silently strip the legacy key.

## Rotation design
1. `ADMIN_API_KEYS=old,new` → rolling restart → both valid.
2. Migrate clients `old → new`.
3. `ADMIN_API_KEYS=new` → rolling restart → `old` revoked.
Rolling restart behind LB = zero downtime; overlap window = no admin client sees 403 mid-rotation.

## Configuration model
- New `ADMIN_API_KEYS` env (comma-separated). `config.ADMIN_API_KEYS_SET: frozenset[str]` = union of `ADMIN_API_KEY` ∪ `split(ADMIN_API_KEYS, ",")` with `.strip()`, empties dropped, deduped.
- `config.ADMIN_API_KEY` retained (compat).
- `config.ADMIN_KEYS_INFO_ENABLED` (default true) gates the diagnostics endpoint.
- Read at process start (existing config model). Hot-reload deferred.

## Validation model
- `auth._is_admin(authorization) -> bool`: resolve bearer; if no token or empty set → False; else compute OR over `hmac.compare_digest(token, k)` for **all** k in the set (no early return) for timing hygiene.
- `auth.admin_fingerprint(token: str) -> str`: `sha256(token.encode()).hexdigest()[:8]` (pure; used by diagnostics for the authenticated bearer).
- `require_admin` signature/contract unchanged (403 on failure). Admin API behavior, quota admin-bypass, all `/admin/*` routes preserved.

## Admin diagnostics
`GET /v1/admin/keys/info` (`require_admin`):
- When `ADMIN_KEYS_INFO_ENABLED` is false → **404** `{detail:"not found"}` (checked after `require_admin`, so non-admins still get 403 first; no info leak about the feature). When enabled → `{admin_keys_configured: len(ADMIN_API_KEYS_SET), authenticated_fingerprint: admin_fingerprint(<bearer>)}`.
- `authenticated_fingerprint` = `sha256(bearer)[:8]` of the key that passed `require_admin` for this call. Never leaks key material. Under existing `/admin/*` rate-limit `admin` category.

## Code areas (all outside frozen `services/`/`orchestrator/`)
- `backend/app/core/config.py` — `ADMIN_API_KEYS_SET`, `ADMIN_KEYS_INFO_ENABLED`.
- `backend/app/v1/auth.py` — `_is_admin` set-based (compare-all); `admin_fingerprint`.
- `backend/app/v1/routes.py` — `GET /v1/admin/keys/info` (gated).
- `backend/app/main.py` — F9 guard uses `ADMIN_API_KEYS_SET`.
- `scripts/serve_api.sh` — require `ADMIN_API_KEY` OR `ADMIN_API_KEYS`.
- tests.
- **No** schema, `db.*`, CAD, frontend, benchmark changes.

## Config knobs (`config.py`)
`ADMIN_API_KEYS` (default ""), `ADMIN_API_KEYS_SET` (derived frozenset), `ADMIN_KEYS_INFO_ENABLED` (default true). `ADMIN_API_KEY` retained.

## Testing
- **Config unit:** union parse — legacy-only; list-only; both; whitespace/empties stripped; dedupe; empty → empty set.
- **Validation unit:** every key in set authenticates; key not in set → False; empty set → False; no/invalid bearer → False; multiple keys all valid (overlap).
- **F9 guard:** default salt + `ADMIN_API_KEYS` set (no legacy) → boot refused; default salt + only legacy `ADMIN_API_KEY` → refused (unchanged); non-default salt → boots; neither set → boots.
- **API:** `/admin/keys/info` → count + fingerprint; fingerprint == `sha256(bearer)[:8]`; non-admin 403; `ADMIN_KEYS_INFO_ENABLED=false` → 404; old+new both reach an admin endpoint during overlap; dropped key → 403.
- **Regression:** existing 8 admin-using tests pass (legacy path intact); /v1 + full suites green; engine-freeze guard empty.

## Non-goals
Hot-reload/SIGHUP; per-key labels/last-used; DB-backed admin keys/scopes/expiry (would add schema); usage metrics; secrets-manager integration — all upgrade-path.

## Upgrade path
Hot-reload for restart-free rotation; per-key labels + last-used in diagnostics; DB-backed admin keys with scopes/expiry; key-usage metrics; secrets-manager.

## Release criteria
/v1 suite passes; full suite passes; engine-freeze guard `git diff v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator` empty; no CAD/frontend/benchmark/schema changes; union model + restart-overlap rotation + gated diagnostics + F9 compatibility all enforced; fingerprint == `sha256(key.encode()).hexdigest()[:8]`.
