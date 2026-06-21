# CAD Capability Benchmark — Evaluation Report

**Date:** 2026-06-19
**Pipeline:** `qwen_claude_code` hierarchical (Qwen plan → decompose → component generate → validate → repair → assemble → validate → artifacts)
**Driver:** real `claude_generation.run()` per object, unmodified pipeline. Capture-only.
**Claude model:** sonnet, `max_turns=15`, `CLAUDE_CODE_MAX_REPAIRS=2`, tools `Read,Write,Edit,Bash`.

> Evaluation report only. No pipeline / orchestration / repair / frontend changes were made.

---

## 1. Per-object results

| # | Object | Path | Stage reached | Pass | Comp pass | Repairs | Assembly | Gen time | Solids | Faces | Edges | BBox (mm) |
|---|--------|------|---------------|------|-----------|---------|----------|----------|--------|-------|-------|-----------|
| 1 | calibration block | single-shot | CLAUDE_CODE_GENERATION | ❌ | n/a | 0 | n/a | 87.2s | — | — | — | — |
| 2 | mounting plate | single-shot | CLAUDE_CODE_GENERATION | ❌ | n/a | 0 | n/a | 45.9s | — | — | — | — |
| 3 | **gear housing** | hierarchical | **COMPLETED** | ✅ | 8/8 | 4 | ✅ | 2777.7s | 8 | 1808 | 5342 | 40×40×28 |
| 4 | quadcopter drone | hierarchical | ASSEMBLY_VALIDATION | ❌ | 8/8 | 6 | ❌ | 3139.6s | — | — | — | — |
| 5 | robotic gripper | hierarchical | ASSEMBLY_VALIDATION | ❌ | 8/8 | 3 | ❌ | 2520.5s | — | — | — | — |
| 6 | camera gimbal | hierarchical | COMPONENT_VALIDATION | ❌ | 7/8 | 4 | — | 1979.0s | — | — | — | — |
| 7 | planetary gearbox | hierarchical | COMPONENT_VALIDATION | ❌ | 7/8 | 6 | — | 3215.4s | — | — | — | — |
| 8 | RC car chassis | hierarchical | ASSEMBLY_VALIDATION | ❌ | 8/8 | 5 | ❌ | 1828.8s | — | — | — | — |
| 9 | robotic arm †| hierarchical | COMPONENT_VALIDATION | ❌ | 0/8 | 24 | — | 87.6s | — | — | — | — |
| 10 | desktop CNC frame †| hierarchical | COMPONENT_VALIDATION | ❌ | 0/8 | 24 | — | 86.4s | — | — | — | — |

† **Objects 9 & 10 are environmentally invalid** — the Claude subscription session quota was exhausted ~5 hrs into the run (`"You've hit your session limit · resets 12:30pm (UTC)"`, every call `num_turns:1`, instant `is_error`). They measure quota, not CAD capability, and are excluded from capability rates below.

Geometry facts (solids/faces/edges/bbox) are recorded only when a run reaches `COMPLETED` (assembly inspection). Only gear housing did.

---

## 2. Aggregate metrics

Rates computed over **8 valid runs** (excluding the 2 quota-blocked); raw 10-object figures in parentheses.

| Metric | Value | Notes |
|--------|-------|-------|
| **Object pass rate** | **12.5%** (1/8) | only gear housing completed end-to-end |
| **Component success rate** | **95.8%** (46/48 components) | 71.9% if quota-blocked included (46/64) |
| **Objects with all components valid** | 66.7% (4/6 hierarchical valid) | gear housing, drone, gripper, RC chassis |
| **Assembly success rate** | **25%** (1/4 reaching assembly) | 4 objects reached assembly; only gear housing passed |
| **Repair success rate — component level** | high | most of the 46 passes recovered via the repair loop |
| **Repair success rate — assembly level** | **0%** (0/3) | every assembly needing repair failed (root cause not repair-fixable) |
| **Repair success rate — object level** | 16.7% (1/6) | only gear housing fully recovered |
| **Average generation time** | **1576.8s** (26.3 min) all 10 | 1949s (32.5 min) over 8 valid; ~43 min over the 6 substantive multi-minute runs |
| **Average repair count** | **7.6** all 10 | 3.5 over 8 valid (the two 24-spam runs are quota artifacts) |

**Key reading:** component generation is strong (95.8%); the system collapses at the **monolithic-call stages** (single-shot generation and whole-assembly generation), not at component CAD.

---

## 3. Difficulty ranking (easiest → hardest, by how far the system got)

1. **gear housing** — only full pass (8/8 + assembly + artifacts)
2. **RC car chassis** — 8/8 components, reached assembly
3. **robotic gripper** — 8/8 components, reached assembly
4. **quadcopter drone** — 8/8 components, reached assembly
5. **camera gimbal** — 7/8 components
6. **planetary gearbox** — 7/8 components
7. **calibration block** — single-shot, blocked at generation
8. **mounting plate** — single-shot, blocked at generation
9. **robotic arm** † — inconclusive (quota)
10. **desktop CNC frame** † — inconclusive (quota)

**Difficulty inversion (important):** the two *simplest* objects (calibration block, mounting plate) rank near the bottom. Difficulty here is **not** driven by object complexity — it is driven by **code path**. The single-shot path (used for simple parts) is more brittle than the hierarchical path, because it depends on one large monolithic Claude call that hits the turn ceiling.

---

## 4. Top 3 failure modes

### #1 — `max_turns` ceiling on monolithic Claude calls (dominant; 5 objects)
Single large Claude calls — **single-shot whole-object generation** (calibration block, mounting plate) and **whole-assembly generation** (drone, gripper, RC chassis) — exceed `max_turns=15`. Claude uses the `Bash` tool to self-test its code, burns turns, hits `num_turns:16` → `subtype:error_max_turns` → `is_error=True` → CLI exit 1 → pipeline marks the stage FAILED.
- Evidence: single-shot — `subtype":"error_max_turns","num_turns":16`; assembly — `"Assembly repair 3/2: claude exit 1, is_error=True"` → `"assembly invalid: claude exit 1, is_error=True"`.
- Note: the model usually *had written* valid source before being killed; the failure is the turn cap + the `is_error` exit, not the geometry. This single mode blocks both the simple-object path and the assembly stage — the largest capability limiter.

### #2 — Claude subscription session/quota exhaustion (2 objects: robotic arm, CNC frame)
After ~5 hrs the account hit its session limit (`"You've hit your session limit · resets 12:30pm (UTC)"`). Every component call returned instantly with `is_error` (`num_turns:1`), producing 0/8 components and 24 wasted repair attempts in ~87s.
- Environmental, not a capability result — those two objects are invalid.
- Secondary finding: the pipeline treats a quota/auth error identically to a CAD failure and spins the **entire** repair budget (3 attempts × 8 components) pointlessly instead of aborting fast.

### #3 — Pseudo-component decomposition for non-domain objects (2 objects: gimbal, gearbox → 7/8)
Only `"quadcopter drone"` has a real domain manifest. All other multi-part objects use **generic decomposition** that slugifies `feature_plan` phrases into "components." Some of those are *features*, not standalone solids:
- gimbal failing "component": `fillets_and_chamfers_on_exposed_edges`
- gearbox failing "component": `keyway_in_the_bore`

These cannot be built as independent positive-volume solids, so they error in build123d and never recover within `MAX_REPAIRS=2`, dropping the object to 7/8 and blocking assembly.

---

## 5. Assessment summary

- **Strength:** per-component generation + the component repair loop are robust (95.8% component pass on valid runs). The hierarchical decomposition approach works.
- **Bottleneck:** anything that depends on a **single large Claude call** (single-shot simple parts, assembly composition) fails on the 15-turn ceiling. This caps the system at ~12.5% end-to-end object pass despite strong component output.
- **Decomposition coverage:** robust only for the one hard-coded domain (drone); generic objects depend on brittle feature-slug decomposition that occasionally emits non-buildable pseudo-components.
- **Operational:** long multi-object batches exhaust the Claude session quota (~9 substantive generations before the cap here), and quota errors are not distinguished from CAD failures.

*No remediation implemented — evaluation only, per scope.*

---

# Post-refactor (2026-06-21) — Deterministic Assembly

After the deterministic-assembly refactor (single-shot + monolithic-assembly Claude calls replaced by a Claude-free assembly that composes validated component STEP files; failure classes FAILED_CAD/QUOTA/TURNS; `reports/assembly_graph.json` artifact). Re-ran a 4-object mini-benchmark through the real pipeline.

## 1. Mini-benchmark results (4 objects)

| Object | Path | Status | Comp pass | Repairs | Assembly | Gen time | Solids | Faces | Edges | BBox (mm) |
|---|---|---|---|---|---|---|---|---|---|---|
| calibration block | 1-node | ✅ COMPLETED | 1/1 | 1 | ✅ | 68.1s | 1 | 30 | 72 | 50×50×10 |
| mounting plate | 1-node | ✅ COMPLETED | 1/1 | 1 | ✅ | 80.9s | 1 | 22 | 52 | 120×80×6 |
| quadcopter drone | hierarchical | ✅ COMPLETED | 8/8 | 2 | ✅ | 1949.2s | 17 | 405 | 1042 | 259×189×102 |
| gear housing | hierarchical | ✅ COMPLETED | 8/8 | 2 | ✅ | 2225.2s | 8 | 495 | 1372 | 105.7×106.5×24 |

**4/4 PASS.** Assembly via deterministic STEP composition (OCC compound builder), zero Claude calls in the assembly stage. `assembly_graph.json` persisted for each.

## 2. Before vs after (same 4 objects)

| Object | Before (2026-06-19) | After (2026-06-21) |
|---|---|---|
| calibration block | ❌ FAILED — `error_max_turns` (single-shot), 0 geometry | ✅ COMPLETED — 1 solid, 30 faces |
| mounting plate | ❌ FAILED — `error_max_turns` (single-shot), 0 geometry | ✅ COMPLETED — 1 solid, 22 faces |
| quadcopter drone | ❌ FAILED — ASSEMBLY_VALIDATION (`claude exit 1, is_error` = max_turns on monolithic assembly), 8/8 components but no assembly | ✅ COMPLETED — 17 solids, 405 faces |
| gear housing | ✅ COMPLETED — 8 solids, 1808 faces (Claude monolithic assembly) | ✅ COMPLETED — 8 solids, 495 faces (deterministic STEP composition) |

Note: gear-housing face count dropped (1808 → 495) because the assembly is now a faithful composition of the 8 *validated* component STEPs rather than a Claude-regenerated monolith. Still 8 solids, non-primitive, valid.

## 3. Pass-rate change

| Class | Before | After |
|---|---|---|
| Simple (calibration block, mounting plate) | 0/2 (0%) | **2/2 (100%)** |
| Hierarchical (drone, gear housing) | 1/2 (50%) | **2/2 (100%)** |
| Overall mini-benchmark | 1/4 (25%) | **4/4 (100%)** |

## 4. Did deterministic assembly eliminate the max_turns assembly failures?

**Yes.** Both monolithic call sites are gone:
- **Single-shot** simple parts now route through a 1-node graph → one small component call + deterministic 1-part export. No whole-object monolithic call → no `error_max_turns` (calibration block, mounting plate now pass).
- **Whole-assembly** generation is now deterministic Python (import_step + OCC compound). Drone previously died at the assembly Claude call (`error_max_turns`); now its assembly is Claude-free and completes (17 solids). Zero `error_max_turns` / `FAILED_TURNS` across all 4 runs.

Validated independently: a 16-instance deterministic drone assembly ran clean (16 solids, no segfault, zero Claude calls); 28 unit tests pass.

## 5. Remaining failure modes after the refactor

1. **Component generation is still the only Claude-bound stage** — each component call must finish within `max_turns=15`. The earlier benchmark showed individual components can still hit `error_max_turns` (controller_deck-class issues). Now isolated to single components (recoverable via repair), no longer fatal to the whole object.
2. **Claude session/quota** still gates long batches. Now classified as `FAILED_QUOTA` with abort-fast (no 24-attempt spin), but a quota-exhausted run still fails. Operational, not a code defect.
3. **Pseudo-component decomposition** (failure mode #3, unchanged — out of scope): generic objects still slugify features into "components" (e.g. `keyway_in_the_bore`); a non-buildable pseudo-component drops an object to N-1/N and blocks assembly. Only `quadcopter drone` has a real domain manifest.
4. **Generic placement is structural, not physical** — non-domain objects get a grid layout (non-overlapping, valid, passes anti-primitive gate) but not a physically meaningful arrangement. Domain rules exist only for drone.
5. **Deterministic assembly composes validated STEPs as-is** — no inter-component boolean fusing/interference resolution; parts are placed, not merged. Acceptable for concept CAD; matters for later manufacturing/URDF stages.

*Refactor scope only: assembly determinism + failure classification. CAD modeling quality, Qwen prompts, decomposition, and frontend unchanged.*

---

# Component turn robustness (2026-06-21) — measured verification

Component calls switched to `tools=Read,Write,Edit` (no Bash) + `max_turns=8` + Edit-targeted repair + per-component metrics. 4-object rerun vs the prior (Bash-on, `max_turns=15`) post-refactor run. **Evidence below; mixed result.**

## Before vs after (same 4 objects)

| Object | BEFORE (Bash on, turns=15) | AFTER (Bash off, turns=8) |
|---|---|---|
| calibration block | COMPLETED, 68.1s, 1 repair | COMPLETED, **30.3s**, **3 turns**, 0 repairs |
| mounting plate | COMPLETED, 80.9s, 1 repair | COMPLETED, **29.4s**, **4 turns**, 0 repairs |
| quadcopter drone | COMPLETED, 1949.2s, 8/8, 2 repairs | **FAILED_TURNS**, 28.4s, 0 components |
| gear housing | COMPLETED, 2225.2s, 8/8, 2 repairs | **FAILED_TURNS**, 24.4s, 0 components |

## Turns per component (after, from `component_metrics.json`)

| Object | reads | writes | turns | result |
|---|---|---|---|---|
| calibration block | 2 | 2 | 3 | valid |
| mounting plate | 4 | 2 | 4 | valid |
| drone (fuselage, 1st comp) | 16 | 0 | 9 → `error_max_turns` | FAILED_TURNS |
| gear housing (1st comp) | 16 | 0 | 9 → `error_max_turns` | FAILED_TURNS |

Before: turns/component were **not instrumented** (`component_metrics.json` did not exist pre-change); the 2026-06-19 run showed components could reach `num_turns:16`.

## Repairs per component (after)
- calibration block: 0 · mounting plate: 0 · drone/gear: n/a (aborted on first component, no repair entered).
- Earlier single-component live test (mounting plate, separate run): 1 repair, turns `[5,3]`. Run-to-run variance present.

## Success rates
| | BEFORE | AFTER |
|---|---|---|
| Simple objects | 2/2 (100%) | 2/2 (100%) |
| Hierarchical objects | 2/2 (100%) | **0/2 (0%)** |
| Overall (4 objects) | 4/4 (100%) | **2/4 (50%)** |
| Component success (within attempted) | — | simple 2/2; hierarchical 0 (never wrote) |

## New regressions
- **drone, gear housing: COMPLETED → FAILED_TURNS.** Evidence: first component's Claude call made **16 Read calls and 0 Write calls**, hit `subtype:error_max_turns` at `num_turns:9` (> cap 8) before writing any source; `failure_class="turns"` → immediate `FAILED_TURNS` abort (as specified). Near-cap event fired: `"Component fuselage near turn cap: 9/8"`.
- Root cause: `max_turns=8` is too tight when a component call reads the cad skill heavily (16 reads here) — the budget is exhausted on skill exploration before the Write. Simple parts happened to read lightly (2–4 reads) and fit.

## Most important question — did Bash removal + constraints improve efficiency without reducing pass rate?

**NO (on this run).** Evidence:
- **Efficiency improved for simple objects:** 68→30s and 81→29s wall-clock (~2.4–2.7×), 3–4 turns, 0 repairs, no Bash self-test loops.
- **Pass rate regressed for hierarchical objects:** 4/4 → 2/4. Bash removal succeeded (0 Bash calls observed), but the `max_turns=8` cap caused skill-heavy component calls to exhaust turns reading before writing → `error_max_turns`.
- The two effects are separable: Bash removal = good; `max_turns=8` = too low for skill-reading components. (Mitigation is a config/prompt change — `CLAUDE_CODE_COMPONENT_MAX_TURNS` is env-overridable — not implemented here per Task 7 = verification only.)

*Verification only. No implementation, prompt, assembly, frontend, or API changes in this step.*

---

# Anti-exploration + turn-budget fix (2026-06-21) — measured

Fix: component prompt rewritten to forbid exploratory reads and mandate `Write`-first (dropped the skill-read line that triggered `/root/.claude/plugins` reads); `CLAUDE_CODE_COMPONENT_MAX_TURNS` 8→12, `NEAR_CAP` 6→8. Bash stays off; Edit-targeted repair, metrics, deterministic assembly unchanged.

## Three-config comparison (same 4 objects)

| Object | Bash-on / turns=15 | Bash-off / turns=8 | **Bash-off / turns=12 + anti-explore** |
|---|---|---|---|
| calibration block | COMPLETED 68.1s | COMPLETED 30.3s | **COMPLETED 30.3s** |
| mounting plate | COMPLETED 80.9s | COMPLETED 29.4s | **COMPLETED 27.2s** |
| quadcopter drone | COMPLETED 1949.2s | **FAILED_TURNS** | **COMPLETED 1232.9s** |
| gear housing | COMPLETED 2225.2s | **FAILED_TURNS** | **COMPLETED 1397.8s** |
| **Pass rate** | 4/4 | **2/4** | **4/4** |

## Turns per component (instrumented)

| Object | Bash-off/8 | **Bash-off/12 + anti-explore** |
|---|---|---|
| calibration block | 3 | **2** |
| mounting plate | 4 | **2** |
| drone (avg/comp) | 9 → `error_max_turns` (fuselage, 0 writes) | **3.1** (8/8 comps, 25 total) |
| gear housing (avg/comp) | 9 → `error_max_turns` (0 writes) | **2.1** (8/8 comps, 17 total) |

Bash-on/15 turns were not instrumented (`component_metrics.json` predates that run). Evidence of behavior change: 3.2 single-component run showed the first tool action is now `Write` with **zero** preceding reads (vs the prior 4–5 exploratory dir/plugin/probe reads).

## Duration

| Object | Bash-on/15 | Bash-off/12+anti-explore | Δ |
|---|---|---|---|
| calibration block | 68.1s | 30.3s | **−55%** |
| mounting plate | 80.9s | 27.2s | **−66%** |
| quadcopter drone | 1949.2s | 1232.9s | **−37%** |
| gear housing | 2225.2s | 1397.8s | **−37%** |

## Repairs per component
calibration 0 · mounting 0 · drone 3 (8 components) · gear 0. (Drone needed 3 component repairs; still completed 8/8.)

## Result vs goal — "keep Bash-removal efficiency AND recover 4/4"
**Achieved.**
- **Pass rate recovered:** 2/4 → **4/4** (drone + gear COMPLETE again). Zero `FAILED_TURNS`.
- **Efficiency retained and improved:** every object faster than the Bash-on/15 baseline (−37% to −66%); avg **2–3 turns/component** (vs 9-and-cap-out before). Write-first eliminated exploratory reads.
- Verification: 34 unit tests pass; 3.2 first-action=Write/2 turns/valid; 3.3 repair Edit-only/4 turns/recovered.

*Fix scope only: component prompt + turn constants. Bash-off, Edit-repair, metrics, deterministic assembly, Qwen prompts, frontend, API all unchanged.*

---

# Error-aware repair hints (2026-06-21) — partial

10-object benchmark (Bash-off/12) failed at object 4: **drone `FAILED_CAD`** — only `arm` failed, build123d `FilletError` (radius 0.2), repair did not converge in 2 attempts (Claude kept the invalid fillet). Fix: `component_validator._repair_hint(reason)` appends build123d-specific remediation to `repair_prompt` for known classes (fillet / chamfer / NameError-AttributeError / empty-degenerate). Commit `417552a`.

## Verification
- Unit: 40 tests pass (6 new repair-hint tests).
- **2.2 fillet convergence (decisive, live):** seeded an arm with `fillet(edges, 1.5)` (guaranteed `FilletError`). With the hint, Claude reduced `FILLET 1.5 → 0.5` via Edit (Bash off), valid in **3 turns / 1 repair** — vs the prior 3-attempt non-convergence. **The targeted class is fixed.**
- **2.3 drone re-run: STILL `FAILED_CAD` (7/8).** Arm passed cleanly this run (2 turns, 0 repairs — fillet not tripped); **`motor_pod` failed** with a *different* error: `TypeError: BuildSketch.__init__() got an unexpected keyword argument 'origin'` (source: `BuildSketch(Plane.XY, origin=loc)`). 8 turns / 2 repairs, no convergence.

## Finding
The drone is nondeterministic — each run a different component trips a different build123d error (run 1: arm fillet; run 2: motor_pod BuildSketch kwarg). The hint helps the classes it matches, but:
- The new `TypeError: ... unexpected keyword argument` is **not** matched by `_repair_hint` (only NameError/AttributeError/"not defined") → fell through to generic → non-convergence.
- There is a **long tail of build123d API-misuse errors**; coverage is incomplete and 2 repairs rarely self-correct API misuse without targeted guidance.

## Status
Fix verified for the fillet class; drone end-to-end **not yet passing**. 10-object benchmark **paused** at object 4 (objects 1–3 PASS: calibration, mounting, gear). Branch not closed — open decision: extend `_repair_hint` to cover `TypeError`/unexpected-keyword (and broaden the build123d API class), then re-run the drone before resuming the benchmark.

*No patch beyond commit `417552a`. No assembly/frontend/API/Qwen changes.*
