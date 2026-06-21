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
