# P3 — Default-Flip Readiness Checklist (2026-06-23)

**Question:** is the platform ready to change the committed default from
`API_WORKER_MODE=single` to `API_WORKER_MODE=claim`?

**Baseline:** `v0.3.0-multiworker-activation`. Foundation = `v0.2.9-multiworker-prep`
(claim/lease/heartbeat/lease-scoped recovery). Engine frozen. This is an assessment
only — no code changed.

---

# 1. Functional Readiness

| Item | Evidence | Verified |
|---|---|---|
| claim-mode validated | `test_v1_worker_claim.py` (claim runs job→completed, claimed_by set) + `test_v1_worker_activation.py` | ✅ |
| duplicate-execution tests passing | `test_two_workers_no_duplicate_execution` (calls.count==1), `test_two_workers_each_job_runs_once` (each once, none twice) | ✅ |
| stale-lease recovery validated | `test_v1_worker_db.py` (expired/NULL lease → pending; unexpired untouched) | ✅ |
| reclaim path validated | `reclaim_expired` requeue-only (never fails); `test_recover_reclaim_logs_and_requeues` | ✅ |
| activation runbook complete | `docs/MULTIWORKER_ACTIVATION_RUNBOOK.md` (Stage 1/2/N + checks) | ✅ |
| rollback runbook complete | runbook §Rollback (drain-then-switch + abrupt) | ✅ |

**Status:** [x] Ready  [ ] Not Ready
*(All functional behavior proven at the worker-loop/DB level in the test suite.)*

---

# 2. Operational Readiness

| Item | Evidence | Verified |
|---|---|---|
| /v1/admin/claims available | `GET /v1/admin/claims` (admin-gated, active-claims view) | ✅ |
| claim/renew/reclaim INFO logs available | logger `app.v1.queue`: `claim`/`renew`/`reclaim` | ✅ |
| worker ownership observable | `claims[].claimed_by` + `by_owner[]` | ✅ |
| lease expiry observable | `claims[].lease_expires_at` + `stale` flag | ✅ |
| reclaim events observable | `reclaim worker=… count=…` log line | ✅ |

**Status:** [x] Ready  [ ] Not Ready
**Caveat:** `/v1/admin/claims` is a **claim view, not a process census** — idle workers
do not appear; a true worker-process registry is a future milestone. Aggregate
cross-process counters are log-derived (no metrics table yet).

---

# 3. Reliability Readiness

| Item | Evidence | Verified |
|---|---|---|
| targeted activation tests passing | 22/22 (`worker_activation` + `worker_claim` + `worker_db` + `claims_api`) | ✅ |
| /v1 suite passing | 135/135 | ✅ |
| full suite passing | 179/179 | ✅ |
| engine-freeze guard empty | `git diff v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator` → empty | ✅ |
| cold restart behavior documented | runbook §How-it-launches + boot-reaper caveat | ✅ |
| rolling restart guidance documented | runbook boot-reaper caveat (prefer rolling over simultaneous cold N-boot) | ✅ |

**Status:** [x] Ready  [ ] Not Ready
*(All green in default single mode; claim-mode reliability proven in tests, not yet in
production.)*

---

# 4. Remaining Risks

### R1 — Boot-reaper kills a sibling's live `claude` child
- **Description:** the F7 reaper runs per process at startup, skips only its own
  PID+descendants, and matches any headless-`claude` under the workspace root. On a
  **simultaneous cold N-boot**, one process's reaper can kill another worker's
  freshly-spawned `claude` child.
- **Impact:** a just-started job is killed → reclaimed → re-runs (no data loss, wasted
  work + a Claude session).
- **Likelihood:** Low — window is the boot instant before any job is claimed; rare
  outside a simultaneous cold start under load.
- **Mitigation:** documented — prefer **rolling restart** (stagger workers); avoid
  simultaneous cold N-boot under load.

### R2 — SQLite write-lock contention
- **Description:** N processes share one SQLite DB; all writes serialize (WAL +
  `busy_timeout=5000ms`).
- **Impact:** at high N / high write rate, claim/renew/terminal writes could queue or
  hit `database is locked` if a write exceeds the busy timeout.
- **Likelihood:** Low at small N (2–3) with short writes; rises with N and job churn.
- **Mitigation:** keep N small on SQLite; validate `database is locked` absence at
  Stage 2/N; cross-host scale path is Postgres (`SELECT FOR UPDATE SKIP LOCKED`),
  explicitly future.

### R3 — Operator misconfiguration (subscription overrun)
- **Description:** effective concurrency = `WORKERS × CLAUDE_CODE_MAX_CONCURRENT`; an
  operator could set it above the Claude subscription session limit.
- **Impact:** subscription exhaustion → jobs fail with `quota`/`session limit`
  failure_class.
- **Likelihood:** Medium — no automatic enforcement (no cross-process global cap).
- **Mitigation:** runbook mandates `WORKERS × CLAUDE_CODE_MAX_CONCURRENT ≤ limit`,
  start N=2; existing quotas/rate-limits cushion; a DB/Redis global running-cap is a
  future upgrade.

### R4 — Claim-mode rollout errors
- **Description:** activating claim mode with a stale/partial deploy, or rolling back
  abruptly, can fail in-flight jobs.
- **Impact:** abrupt rollback marks in-flight `running` jobs `failed` (re-submittable);
  no corruption.
- **Likelihood:** Medium during first rollout.
- **Mitigation:** staged runbook (Stage 1 single-process claim first), drain-then-switch
  as the default rollback, abrupt switch documented as lossy-but-safe break-glass.

---

# 5. Production Rollout Recommendation

**Choice: A. Keep default = `single`** (for now).

**Justification:** the mechanism is fully validated **in the test suite** and
operationally observable, but **zero production evidence** exists for claim mode under
real concurrency — no real `WORKERS=2` run, no real rolling-restart, no observed
production reclaim, no executed rollback drill. The default governs every deployment;
flipping it before production validation would expose all environments to the
unproven multi-process path and the open risks (R1–R4). Keep `single` as the committed
default; activate `claim` per the runbook in the target environment, gather the
Section-6 evidence, then flip in a later, trivial change. Cost of waiting is low
(opt-in already works); cost of a premature flip is broad.

---

# 6. Required Evidence Before Flip

Collect ALL of the following in the actual production (or production-equivalent)
environment before changing the default:

1. **Successful Stage-1 run** — `WORKERS=1`, `API_WORKER_MODE=claim`: real jobs reach
   `completed`; outcomes match single mode; no `database is locked`.
2. **Successful `WORKERS=2` run** — two distinct `claimed_by` in `/v1/admin/claims`;
   sustained job throughput; **zero duplicate executions** observed over a meaningful
   job volume.
3. **Successful rolling restart** — workers staggered; no sibling `claude` child killed
   by the boot reaper (R1 not triggered).
4. **Successful reclaim event** — a `kill -9`'d worker's job observed transitioning via
   lease expiry → `reclaim` log → re-run to completion (R-recovery proven live).
5. **Successful rollback drill** — drain-then-switch executed with **zero failed jobs**;
   abrupt switch executed once and confirmed lossy-but-safe (jobs `failed`/re-submittable,
   no stuck rows).
6. **Concurrency-cap adherence** — confirmed `WORKERS × CLAUDE_CODE_MAX_CONCURRENT ≤
   subscription limit` with no `quota`/`session limit` failures during the run (R3).
7. **Lease stability under load** — `/v1/admin/claims` shows leases renewing (not
   trending to `stale`) while jobs run.

---

# 7. Final Recommendation

**CONDITIONALLY READY.**

**Rationale:** Functional, operational, and reliability readiness are all **met in the
test suite** (Sections 1–3 green: 22/22 targeted, 135/135 /v1, 179/179 full, guard
empty; full observability + runbooks). The foundation is safe by construction (atomic
single-winner claim, requeue-only lease recovery, WAL + busy_timeout). What is missing
is **production evidence** — the Section-6 observations have not yet been gathered in a
live multi-process deployment. Therefore: **do not flip the default yet** (keep
`single`); activate `claim` via the runbook in the target environment, collect the
Section-6 evidence (especially zero-duplicate-execution at `WORKERS=2`, a live reclaim,
and a rollback drill), then the default flip becomes a low-risk config change. Open
risks R1–R4 are all documented with mitigations and none is a blocker to *activation*;
they are reasons to validate in production before making `claim` the universal default.
