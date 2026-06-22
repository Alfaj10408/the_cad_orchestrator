# Spec — F6: Readiness Health Checks (P2)

**Date:** 2026-06-22
**Status:** Approved design → implementation plan
**Baseline:** `v0.2.2-f4-dynamic-queue-pos`. Engine frozen (`v0.1-benchmark-10of10`).
**Goal:** `readyz` reflects true readiness of all dependencies the configured generation provider needs, cheaply and without inference.

## Problem
`readyz` checks only db, an ungated claude_code, and worker; it misses orchestrator/Qwen availability, model config, storage writability, and disk — and always returns HTTP 200. The ungated claude check also false-fails in deterministic mode.

## Decisions (approved)
1. `readyz` → **HTTP 200 when ready, 503 when not ready** (body always present).
2. Heavy checks (orchestrator, claude) **TTL-cached**, `API_READYZ_CACHE_S = 15`.
3. **Provider-aware gating:** orchestrator checked only if `ORCHESTRATOR_ENABLED`; claude only if `CLAUDE_CODE_ENABLED`; otherwise `"skipped"` (not counted).
4. **Add a `timestamp`** (UTC ISO) to the readyz response.

## Model
- **`healthz`** → `{ok:true}`, lightweight, no I/O. Unchanged.
- **`readyz`** → `{ready, checks{...}, timestamp}`; `ready = all non-skipped checks are True`; status 200 if ready else 503.

| check | gating | how (cheap, no inference) |
|---|---|---|
| `db` | always | `db.connect()`+`SELECT 1`+close |
| `queue` | always | `app.state.queue.alive()` (in-memory) |
| `storage` | always | write+delete a temp file under `config.PROJECTS_ROOT` |
| `disk` | always | `shutil.disk_usage(config.STORAGE_ROOT).free >= API_MIN_DISK_MB·MB` |
| `orchestrator` | if `ORCHESTRATOR_ENABLED` | reuse `app.ai.llm.client.health()["ok"]` (GET `ORCH_BASE_URL/models` + model served; short timeout, no inference); else `"skipped"` |
| `claude_code` | if `CLAUDE_CODE_ENABLED` | `claude_code_adapter.health()` → `installed and authenticated`; else `"skipped"` |

Check value ∈ `True | False | "skipped"`. Heavy checks (orchestrator, claude) cached `API_READYZ_CACHE_S` seconds (monotonic-clock TTL) so frequent probes don't repeat the network GET / auth subprocess. Cheap checks (db/queue/storage/disk) run every call. All heavy checks are try/except-wrapped with timeouts → readyz never hangs.

## Explicitly avoided
No model inference, no Claude generation, no benchmark execution. Orchestrator = one short-timeout `GET /models`; claude = installed+auth status. Heavy checks TTL-cached.

## Code areas
- `backend/app/v1/routes.py` — rewrite `readyz` (per-check helpers + `_cached` TTL + gating + 503 + timestamp); `healthz` unchanged. Imports `app.ai.llm.client`/`config` and `claude_code_adapter` (all outside frozen `services/`/`orchestrator/`).
- `backend/app/core/config.py` — add `API_MIN_DISK_MB` (default 500), `API_READYZ_CACHE_S` (default 15).
- `backend/app/main.py` — no change (worker liveness via `alive()`; a lifespan start failure aborts the app entirely, covered by app-down).
- tests only otherwise.

## Non-goals
No schema change; no `db.*` signature change; no `/v1` job-contract change; no CAD/frontend/benchmark/queue.py changes; no `ORCH_*`/`CLAUDE_*` config-default changes.

## Testing
- Success (deps disabled or healthy) → `ready:true`, 200, `timestamp` present, disabled deps `"skipped"`.
- DB down (monkeypatch `db.connect` raise) → 503, `db:false`.
- Queue down (fake `alive()→False`) → 503.
- Storage unwritable (point `config.PROJECTS_ROOT` at an unwritable path) → 503, `storage:false`.
- Low disk (monkeypatch `shutil.disk_usage` → tiny free) → 503, `disk:false`.
- Orchestrator enabled+unreachable (monkeypatch `client.health → {"ok":False}`) → 503; disabled → `"skipped"`, not failing.
- Claude enabled+unauth (monkeypatch `adapter.health → {installed:True, authenticated:False}`) → 503; disabled → `"skipped"`.
- `healthz` → 200 `{ok:true}`.
- (Tests set `API_READYZ_CACHE_S=0` or clear the cache between assertions to avoid stale cached heavy-check values.)

## Release criteria
/v1 suite passes; full suite passes; engine-freeze guard `git diff v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator` empty; no CAD/frontend/benchmark/schema changes.
