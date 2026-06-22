# P2 — F6 (readyz dependency checks) Status (2026-06-22)

Third P2 item complete. `readyz` now reflects real dependency readiness, provider-aware, with proper 200/503 semantics. Engine frozen; `healthz`, schema, queue.py, and CAD pipeline unchanged.

## Problem
`readyz` checked only db, an ungated claude_code, and worker, always returned HTTP 200, and missed orchestrator/Qwen availability, model config, storage writability, and disk. The ungated claude check also false-failed in deterministic mode.

## Design (approved decisions)
- `readyz` → **200 ready / 503 not-ready**; body `{ready, checks, timestamp}`.
- Heavy checks (orchestrator, claude) **TTL-cached**, `API_READYZ_CACHE_S=15`; cheap checks every call.
- **Provider-aware gating:** orchestrator only if `ORCHESTRATOR_ENABLED`, claude only if `CLAUDE_CODE_ENABLED`, else `"skipped"` (not counted).
- **`timestamp`** (UTC ISO) generated **per response** (never cached).

## Implementation
- `config.py`: `API_MIN_DISK_MB=500`, `API_READYZ_CACHE_S=15`.
- `routes.py`: `readyz` rewritten with helpers `_check_db`/`_check_storage`/`_check_disk`/`_check_claude`/`_check_orchestrator` (all exception-safe → `False`), `_cached` (monotonic-TTL, only the two heavy checks), gating, `JSONResponse` 200/503, fresh timestamp. `healthz` unchanged.
- Checks: `db` (SELECT 1), `queue` (`alive()`), `storage` (temp write+delete under `PROJECTS_ROOT`), `disk` (`shutil.disk_usage(STORAGE_ROOT).free >= API_MIN_DISK_MB`), `orchestrator` (`app.ai.llm.client.health().ok` — GET /models, **no inference**), `claude_code` (`claude_code_adapter.health()` installed+authenticated, **no generation**).
- Reuses `app.ai.llm.client`/`config` + `claude_code_adapter` (outside frozen `services/`/`orchestrator/`). No schema/`db.*`-signature changes.

## Tests
- `test_v1_readyz.py` (10): success (deps disabled→skipped→200), healthz lightweight, db-down 503, queue-down 503, low-disk 503, orchestrator enabled+unreachable 503, claude enabled+unauth 503, storage-unwritable 503, orchestrator-raises→503-not-500, timestamp present. Cache neutralized in tests (`API_READYZ_CACHE_S=0` + cache clear).
- **/v1 suite: 32/32 · full suite: 76/76.**

## Review outcome (opus whole-branch)
**GO. No Critical/High.** Verified: ready/503 logic, gating, monotonic-TTL cache of only heavy checks, fresh timestamp, exception-safe cheap+heavy checks (orchestrator double-wrapped after the 503-not-500 fix), healthz untouched, engine-freeze empty.
- **Medium (release-note, not a code defect):** readyz response shape changed deliberately — `worker` key renamed → `queue`; added `storage`/`disk`/`orchestrator`/`claude_code`/`timestamp`; now emits **503** when not ready. External probe configs keyed on `checks.worker` or assuming always-200 must update. (Only in-repo consumer updated.)
- **Low:** cache 15s staleness window (by design); storage temp-file leak only if unlink-after-write fails (harmless); Claude first-call probe can block up to ~35s (CLI subprocess timeouts) before caching — fine for readiness, avoid aggressive polling.

## Remaining P2 work (not started)
- **F7** single-instance recovery assumption; reap stale `claude` children on boot.
- **F8** project/brief created before queue-full check → orphan dir on 429.
- **F9** enforce `API_KEY_SALT` at startup.
- **F10** admin-key rotation/revocation.
- **F11** `metrics_json` on failed jobs.
- **F4 follow-up:** monotonic insert-sequence tiebreak for `queue_pos`.
- Deferred features: per-user quota enforcement, token-bucket rate limits, retention sweeper, aggregate admin metrics endpoint.

## Commits (F6, off plan `f3af139`)
`0e5ee9e` readyz deps checks + 200/503 + timestamp · `9c5c069` exception-safe `_check_orchestrator` (503 not 500) + storage/raise tests.
Tag: `v0.2.3-f6-readyz-hardening`.

*P2/F6 only. No schema, frontend, benchmark, queue.py, or CAD changes; healthz unchanged.*
