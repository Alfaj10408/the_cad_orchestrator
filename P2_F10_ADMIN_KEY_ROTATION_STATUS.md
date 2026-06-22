# P2 — F10 Admin Key Rotation Status (2026-06-22)

Eighth P2 milestone complete. Multiple concurrent admin keys via `ADMIN_API_KEYS` (unioned with the legacy `ADMIN_API_KEY`) enable zero-downtime rotation + revocation, plus a gated `GET /v1/admin/keys/info` diagnostics endpoint. Engine frozen; CAD pipeline, schema, frontend, and benchmark unchanged. No schema change.

## Problem
`config.ADMIN_API_KEY` was a single env string; `auth._is_admin` did one `compare_digest`. Replacing the key was a hard cutover that broke every in-flight admin client — no overlap window, no per-key revoke.

## Design (approved decisions)
- **Union model:** effective admin set = `ADMIN_API_KEY` (legacy) ∪ `ADMIN_API_KEYS` (comma list), deduped, empties dropped. Backward-compatible.
- **Restart-based + overlap** rotation (admin set derived from config attributes set at process start; rolling restart + overlap window = zero downtime). No hot-reload.
- **Gated diagnostics** `GET /v1/admin/keys/info` (`ADMIN_KEYS_INFO_ENABLED`, default true).
- **F9 compatibility:** boot guard refuses default salt when the effective admin set is non-empty (either env var).
- **No schema change**; keys stay in env (never DB/logs); fingerprint non-reversible.
- Validation compares **all** keys with `compare_digest`, OR-accumulates (no short-circuit). Fingerprint `sha256(key.encode()).hexdigest()[:8]`. Engine frozen; no CAD/frontend/benchmark changes.

## Implementation
- `config.py`: `ADMIN_API_KEYS` (default ""), `ADMIN_KEYS_INFO_ENABLED` (default true), `admin_key_set() -> frozenset` (unions bare module-level `ADMIN_API_KEY` + comma-split `ADMIN_API_KEYS`; reads config attrs, not `os.environ`). `ADMIN_API_KEY` retained.
- `auth.py`: `_is_admin` rewritten set-based (`ok=False; for k in admin_key_set(): if compare_digest: ok=True; return ok` — no `any()`/early-return); `admin_fingerprint(token) = sha256(token.encode()).hexdigest()[:8]`. `require_admin` contract unchanged.
- `routes.py`: `GET /v1/admin/keys/info` (require_admin; disabled → 404 after admin gate; else `{admin_keys_configured, authenticated_fingerprint}`).
- `main.py`: F9 guard → `config.admin_key_set()`.
- `scripts/serve_api.sh`: require `ADMIN_API_KEY` OR `ADMIN_API_KEYS`; `API_KEY_SALT` non-default guard preserved.

## Rotation model
1. `ADMIN_API_KEYS=old,new` → rolling restart → both valid (overlap).
2. Migrate admin clients `old → new`.
3. `ADMIN_API_KEYS=new` → rolling restart → `old` revoked.
Rolling restart behind the LB = zero downtime; the overlap window means no admin client sees a 403 mid-rotation. Revocation = drop the key from the set + restart (env is the single source of truth → immediate on restart).

## Config model
- `ADMIN_API_KEYS` (comma-separated). `admin_key_set()` = union of `ADMIN_API_KEY` ∪ `split(ADMIN_API_KEYS, ",")` with `.strip()`, empties dropped, deduped → `frozenset`.
- Derived **live from config attributes** (set at process start), not re-read from `os.environ` per request — honors restart-based reading while keeping legacy tests that monkeypatch `config.ADMIN_API_KEY` working untouched.
- `ADMIN_KEYS_INFO_ENABLED` (default true) gates the diagnostics endpoint. `ADMIN_API_KEY` retained for compat.

## Diagnostics endpoint
`GET /v1/admin/keys/info` (`require_admin`, runs first → non-admin 403 even when disabled). When `ADMIN_KEYS_INFO_ENABLED` false → 404. When enabled → `{admin_keys_configured: len(admin_key_set()), authenticated_fingerprint: sha256(<this request's bearer>)[:8]}`. Non-reversible fingerprint — never leaks key material; lets an operator confirm the overlap window (count==2) and which key a client uses. Under existing `/v1/admin/*` rate-limit `admin` category.

## F9 compatibility
Boot guard: `if config.API_KEY_SALT == "dev-salt-change-me" and config.admin_key_set(): raise`. Fires on default salt with **either** env var non-empty; non-default salt boots; no-admin dev boots. The 3 original F9 tests (legacy `ADMIN_API_KEY`) still pass; a new case covers the `ADMIN_API_KEYS`-only path via the lifespan `TestClient` mechanism.

## Tests
- `test_v1_admin_keys_unit.py` (12): union parse (legacy-only/list-only/both/strip/dedupe/empty); `_is_admin` any-key/legacy/not-in-set/empty/no-bearer; fingerprint format.
- `test_v1_admin_keys_api.py` (5): count+fingerprint; per-key fingerprint; require_admin 403; disabled→404 (admin-gated first); overlap both keys 200.
- `test_v1_f9_salt.py` (4): default-salt+legacy refuses; strong-salt boots; dev no-admin boots; **default-salt+ADMIN_API_KEYS refuses** (new).
- Regression: existing 8 admin-using tests pass (legacy monkeypatch path intact via `admin_key_set()`).
- **F9 4/4 · /v1 suite: 113/113 · full suite: 157/157 · engine-freeze guard empty.**

## Review outcome (opus whole-branch)
**GO. No Critical/High/Medium.** Security verified: compare-all OR-accumulate (no short-circuit → leaks only key count, never which); non-reversible fingerprint; diagnostics admin-gated-first-then-flag (403 before 404); revocation via env source-of-truth on restart; comma-split values used only in `compare_digest` (no injection); non-admin cannot escalate. F9 dual-env guard + serve_api.sh OR-requirement correct. `require_admin` contract unchanged; legacy single-key path intact. No schema/engine changes; guard empty.
- **Lows (deferred, cosmetic):** `admin_key_set()` annotation bare `frozenset` vs `frozenset[str]`; PEP8 blank-line spacing between auth functions.

## Remaining roadmap (not started)
- **Multi-worker prep** — strategic next: atomic job claiming (`claimed_by`/lease), drop `recover()`'s "fail-all-running" assumption, advisory locks; introduces the first justified schema change. Own design proposal.
- **F4 follow-up** — monotonic insert-sequence tiebreak for `queue_pos`.
- **F10 upgrade path** — hot-reload/SIGHUP for restart-free rotation; per-key labels + last-used in diagnostics; DB-backed admin keys with scopes/expiry (schema); key-usage metrics; secrets-manager integration.
- **F7/F11 upgrade** — periodic reaper; failure time-series; owner-job-view metrics.
- **Rate-limit / quota / retention upgrades** — Redis backends, per-key/per-user overrides, multi-instance coordination.
- **P3** — multi-worker scaling.

## Commits (off `29f5738`)
`6972852` admin key set (union) + set-based validation + fingerprint · `c43f0a1` gated keys/info diagnostics endpoint · `fc13ba1` F9 guard + serve_api.sh accept ADMIN_API_KEYS.
Tag: `v0.2.8-admin-key-rotation`.

*P2 F10 admin key rotation only. No CAD, frontend, benchmark, schema, or engine changes.*
