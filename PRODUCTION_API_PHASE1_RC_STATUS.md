# Production API Phase 1 — Release-Candidate Status (2026-06-22)

Follow-up to `PRODUCTION_API_PHASE1_REVIEW.md`: the 2 High + 1 Medium findings are fixed. No High/Critical remain → **release-candidate ready** (single-instance/single-worker Phase 1).

## Fixed findings
| ID | Sev | Fix | Commit |
|---|---|---|---|
| **F1** | HIGH | Artifact download now keyed by the listing's `relative_path` via `{rel:path}`; serves `cad/`, `meshes/`, `reports/` etc. Two-layer traversal guard (pre-resolve `is_absolute()`/`..` + post-resolve `root in target.parents`) + `is_file()`. | `4bbca4b` |
| **F2** | HIGH | `CORSMiddleware` installed in `main.py`, driven by `V1_CORS_ORIGINS`, gated to non-empty (default-closed), `allow_credentials=False`. | `21bd9c2` |
| **F3** | MEDIUM | Worker re-reads the job row before writing terminal status; skips overwrite if `cancelled` → a cancelled job can no longer flip to `completed`/`failed`. | `e934281` |

Each fix: implemented → targeted test added → reviewed (clean) → committed. All confined to `backend/app/v1/` + `backend/app/main.py`; **no `services/`/`orchestrator/` changes**.

## Remaining findings (deferred to P2 — none High/Critical)
- **F4 (MED)** `queue_pos` stale (recorded at enqueue, not updated). Document as "position at submission" or compute on read.
- **F5 (MED)** single shared SQLite connection across async worker + threadpool routes — give the worker its own connection / lock before P3 concurrency.
- **F6 (LOW)** `readyz` omits Qwen + disk checks.
- **F7 (LOW)** restart recovery assumes a single instance; stale `claude` children not reaped on hard crash.
- **F8 (LOW)** project/brief created before queue-full check → orphan dir on 429.
- **F9 (LOW)** `API_KEY_SALT` default not enforced at startup (defense-in-depth; high-entropy keys make brute force infeasible regardless).
- **F10 (LOW)** static admin secret, no rotation/revocation.
- **F11 (LOW)** `metrics_json` only on normal completion.

Rationale for deferring: all are hardening/observability or load-only concerns; none affect correctness, auth, ownership, or the engine for a single-instance Phase 1 deployment. F5 is the priority before raising concurrency (P3).

## Test results
- **Engine-freeze guard:** `git diff v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator` → **EMPTY** (re-verified). CAD engine untouched.
- **/v1 suite: 15/15 passed** (was 10; +5 from F1/F2/F3 tests).
- **Full suite: 59/59 passed** (was 54; +5). All existing CAD/benchmark/orchestrator tests unchanged + green.
- Verified-correct (carried from review): cross-user 404 on all job routes; path-traversal safe; constant-time admin compare; revoked-key SQL exclusion; resilient worker.

## Release recommendation
**GO (release-candidate).** All High findings resolved; engine frozen; auth/ownership/CORS/traversal correct; full + /v1 suites green. Ship Phase 1 under the documented **single-instance, single-worker** assumptions. Address F5 before enabling P3 parallel generation. Remaining Low items are routine P2 hardening.

## Commits (RC fixes)
`4bbca4b` F1 · `21bd9c2` F2 · `e934281` F3 (each with tests + review).

*RC verification only. No P2. No benchmark reruns. No frontend changes. No engine changes.*
