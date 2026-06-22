# Production API Phase 1 — Whole-Branch Review

**Range:** `v0.1-benchmark-10of10` (`c214d1f`) → HEAD `d65a2c8` (11 commits)
**Reviewer:** final whole-branch (opus). No code modified during review.
**Engine-frozen guard:** `git diff v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator` → **EMPTY** (re-verified). CAD engine untouched; all changes in `backend/app/v1/*` (new), `config.py` (+8), `main.py` (lifespan+router), `scripts/serve_api.sh`, tests, docs.

## Outcome
**2 High, 0 Critical**, plus 3 Medium and 6 Low/Info. Per the review protocol (High/Critical → stop), the branch is **NOT release-candidate-ready as-is**. No fixes applied (review-only). Recommendation: **Conditional GO** — fix the 2 Highs (+ ideally F3) before push; the rest are documented P2 hardening.

## Findings

### F1 — HIGH — Artifact download works only for STEP; STL/GLB/reports/snapshots 404
`backend/app/v1/routes.py:85` hardcodes `target = (root / "cad" / name)`. But `artifact_service.list_artifacts` rglobs the **whole** project tree (`cad/`, `meshes/`, `reports/`, `source/`). So every non-`cad/` artifact the list endpoint advertises is **undownloadable** via `/v1`; breaks the "STEP → STL/GLB → report" deliverable chain through the facade. Basename-collision risk across dirs too.
**Fix:** key download by the listing's `relative_path` (e.g. `?path=<rel>`), resolve `(root / rel).resolve()`, keep `root in target.parents` containment + reject absolute/`..` (mirror `artifact_service._resolve`). **Confirmed real.**

### F2 — HIGH — `V1_CORS_ORIGINS` is dead config; no CORS middleware installed
`config.py:74` parses `V1_CORS_ORIGINS`; spec (Security P1) promises a CORS allowlist. **No `CORSMiddleware` exists** (grep over `backend/app/` → none). The env var is silently ignored — a browser-facing prod API ships without the stated CORS control.
**Fix:** add `CORSMiddleware` in `main.py` driven by `V1_CORS_ORIGINS` (when non-empty); OR if CORS is delegated to the reverse proxy, delete the config key and amend the spec. Don't ship dead security config. **Confirmed real.**

### F3 — MEDIUM — Cancel of a running job can be overwritten / is best-effort
`routes.py:519-525` sets `cancelled` + `claude_code_adapter.cancel`. (a) The worker later writes the pipeline's terminal status unconditionally → a cancelled job can land `completed`/`failed` (no "skip if cancelled" guard). (b) `cancel` only kills a registered component child; between stages (assembly/mesh/snapshot) it returns False and `run()` continues.
**Fix:** worker re-reads the row and skips the update if `status == "cancelled"`; document cancel as best-effort outside active Claude calls.

### F4 — MEDIUM — `queue_pos` recorded once at enqueue, never updated
`routes.py:501`. Stale for queued jobs; not nulled on `running`. Spec implies live position.
**Fix:** compute on read or null at `running` (or document as "position at submission").

### F5 — MEDIUM — Single shared SQLite connection written from async worker + threadpool routes
`db.connect(check_same_thread=False)` shared via `app.state.db`; sync routes run in a threadpool, worker is async. Concurrent `commit()`s on one connection are not thread-safe in general (WAL helps multi-*connection*, not a shared one). Low risk at single-worker/low-QPS; latent under load.
**Fix:** per-context connections (worker owns its own) or a write lock — required before P3 concurrency.

### F6 — LOW — `readyz` omits Qwen/orchestrator + disk checks (spec lists them)
`routes.py:561-576` checks only db/claude_code/worker. With `qwen_claude_code` default, a down orchestrator still reports `ready:true`.
**Fix:** add a best-effort orchestrator ping + disk-writability check, or trim the spec.

### F7 — LOW — Restart recovery assumes a single instance; stale OS children not reaped
`queue.recover` marks all `running` → `failed/internal` + re-enqueues `pending`. Correct for single-instance cold restart; two instances sharing the DB would corrupt each other. Hard-crash `claude` children may leak.
**Fix:** document single-instance assumption; optionally pkill stray `claude` on boot. Ship-acceptable.

### F8 — LOW — Project/brief created before queue-full check → orphan dirs on 429
`routes.py:486-500` writes project + brief + job before `enqueue`; 429 path marks DB failed but leaves the on-disk project. Slow disk leak under sustained overload.
**Fix:** check `depth()` before creating, or clean up on 429.

### F9 — LOW — `API_KEY_SALT` defaults to `"dev-salt-change-me"`; not enforced at startup
`config.py`; `serve_api.sh` enforces `ADMIN_API_KEY` but not the salt. Known salt weakens at-rest hashes (defense-in-depth only — `sk_`+32-byte tokens make brute force infeasible regardless). User-key lookup is indexed hash equality (no practical timing leak); admin compare uses `hmac.compare_digest` (correct).
**Fix:** add `: "${API_KEY_SALT:?...}"` to `serve_api.sh` or fail-fast if default in prod.

### F10 — LOW — Admin secret is a long-lived static Bearer with no rotation/revocation
`auth._is_admin` compares raw `ADMIN_API_KEY` (constant-time). Fine for Phase-1 bootstrap; rotation = env change + restart. Note for P2.

### F11 — LOW/INFO — `metrics_json` only set on normal completion (timeout/exception paths skip it)
Minor observability gap on failed jobs. Spec says "at completion" — acceptable.

## Verified-correct (no action)
- Engine frozen (empty services/orchestrator diff). ✓
- Ownership: `_owned_row` enforces cross-user **404** on every job route (status/cancel/artifacts/events). ✓
- Path traversal: `root in target.parents` correctly rejects `..`; `{name}` isn't a `:path` param so `/` won't route. ✓
- Admin compare constant-time (`hmac.compare_digest`); empty `ADMIN_API_KEY` disables admin. ✓
- Revoked keys excluded at SQL (`revoked_at IS NULL`). ✓
- Queue worker resilient (per-job exceptions caught, loop continues; `CancelledError` escapes correctly). ✓
- Tests: /v1 10/10, full 54/54 (reported). ✓

## Must-fix before push
- **F1 (HIGH)** — fix artifact download (key by relative_path).
- **F2 (HIGH)** — install CORS middleware or remove the dead config + amend spec.
- Strongly recommended: **F3 (MEDIUM)** — cancel-overwrite guard (user-visible contract violation).

## Can-fix later (P2 hardening — document assumptions)
F4 (queue_pos), F5 (per-context DB connections — before P3), F6 (readyz Qwen/disk), F7 (single-instance recovery), F8 (orphan on 429), F9 (salt fail-fast), F10 (admin rotation), F11 (metrics on failure).

## Critical or High present?
**YES — 2 High (F1, F2), 0 Critical.** Per protocol: **STOP** — reported, no fixes applied, no push, P2 not started.

## Final recommendation
**Conditional GO.** Architecture sound; engine-frozen guarantee holds; auth/ownership/traversal correct; tests green. **Not push-ready until F1 + F2 (small, localized edits in `routes.py`/`main.py`) are fixed**, ideally F3 too. After those, Phase 1 is releasable under the documented single-instance/single-worker assumptions; remaining items are P2.
