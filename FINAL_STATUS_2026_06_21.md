# Final Status — 2026-06-21

Clean project checkpoint after the **deterministic assembly refactor**. End of this phase.

---

## Architecture overview

`qwen_claude_code` pipeline — Qwen plans, Claude builds components, backend assembles deterministically.

```
brief → Qwen work order → design_spec + component_manifest
  → per-component generation     (small Claude call, ONE part each)   ← only Claude in loop
  → per-component validation     (STEP export + inspect + gate)
  → assembly graph        [deterministic]  → reports/assembly_graph.json
  → placement resolution  [deterministic, 0 Claude]
  → assembly compose+export [deterministic]  (import_step of validated STEPs → OCC compound)
  → assembly validation   (anti-primitive + node-count + bbox-envelope, fails closed)
  → artifacts (STEP/STL/GLB/snapshot)
```

Key change this phase: the two **monolithic Claude call sites** (single-shot whole-object generation; whole-assembly generation) were removed. All objects now route through the hierarchical path — simple parts as a 1-node graph. Assembly is pure Python composing the already-validated component STEP files; it makes **zero** Claude calls.

New modules (`backend/app/orchestrator/`): `assembly_graph.py`, `placement_rules.py`, `assembly_composer.py`. Modified: `assembly_builder.py` (validation extended, Claude prompts removed), `claude_generation.py` (deterministic assembly stage, failure-class wiring, single-shot removed), `claude_code_adapter.py` (failure classification).

Failure taxonomy: `FAILED_CAD` / `FAILED_QUOTA` (abort-fast) / `FAILED_TURNS`.

New artifact: `reports/assembly_graph.json` (node ids, parent, component_type, placement transforms) — seed for future URDF/SRDF.

---

## Benchmark results (4-object mini-benchmark, 2026-06-21)

| Object | Path | Status | Comp | Repairs | Asm | Time | Solids | Faces | Edges | BBox mm |
|---|---|---|---|---|---|---|---|---|---|---|
| calibration block | 1-node | ✅ COMPLETED | 1/1 | 1 | ✅ | 68s | 1 | 30 | 72 | 50×50×10 |
| mounting plate | 1-node | ✅ COMPLETED | 1/1 | 1 | ✅ | 81s | 1 | 22 | 52 | 120×80×6 |
| quadcopter drone | hierarchical | ✅ COMPLETED | 8/8 | 2 | ✅ | 1949s | 17 | 405 | 1042 | 259×189×102 |
| gear housing | hierarchical | ✅ COMPLETED | 8/8 | 2 | ✅ | 2225s | 8 | 495 | 1372 | 105.7×106.5×24 |

**4/4 PASS.** Assembly Claude calls: 0. Full detail + before/after in `benchmark_summary.md`.

### Before vs after (same 4 objects)

| Object | Before (06-19) | After (06-21) |
|---|---|---|
| calibration block | ❌ `error_max_turns` | ✅ COMPLETED |
| mounting plate | ❌ `error_max_turns` | ✅ COMPLETED |
| quadcopter drone | ❌ assembly max_turns | ✅ COMPLETED (17 solids) |
| gear housing | ✅ COMPLETED | ✅ COMPLETED |

---

## Key metrics

| Metric | Before | After |
|---|---|---|
| Simple-object pass | 0/2 (0%) | **2/2 (100%)** |
| Hierarchical pass | 1/2 (50%) | **2/2 (100%)** |
| Mini-benchmark overall | 1/4 (25%) | **4/4 (100%)** |
| Assembly Claude calls | 1 large per assembly | **0** |
| `error_max_turns` failures | dominant | **eliminated** |
| Unit tests | n/a | **28 passing** |
| Independent gate | — | 16-instance deterministic drone assembly, no segfault, 0 Claude calls |

---

## Commits created (off baseline `2134994`)

```
dc8a397 docs(benchmark): post-refactor mini-benchmark — 4/4 pass, max_turns assembly failures eliminated
73c1b03 fix(review): assembly gate fails closed without inspection; remove single-shot dead code; map composer errors to FAILED_CAD
6211490 test: pin composer compound form to Compound(_c)
85924b5 refactor: route simple parts through 1-node graph; remove single-shot branch
cda7196 fix(review): attempt-all components before FAILED_CAD; clarify _run_cad_trusted; bbox-exceeds test
2a42b1f feat: deterministic assembly stage + failure-class wiring; drop Claude assembly prompts
563b358 feat: classify Claude failures (quota/turns/cad)
537708c test: AST-based banned-import check in assembly composer test (fix brittle substring)
b960b32 feat: assembly composer (STEP import)
a58908c feat: deterministic placement rules
1a81e40 feat: assembly graph builder
ea9f354 docs(plan): align section headings to Task N for SDD brief extraction
```
Baseline `2134994` = backend before refactor (incl. prior fixes: component path prefix, error-tail). Repo: fresh product-root git (`backend/` + `tests/` + `docs/` tracked; `storage/`, `runs/`, `logs/` ignored). No remote.

Process: subagent-driven (implement → review → fix per task), final opus whole-branch review (sound/merge-ready, all findings fixed).

---

## Remaining failure modes

1. **Component generation** is the only Claude-bound stage; a single component can still hit `error_max_turns` — now isolated and repair-recoverable, not fatal to the whole object.
2. **Claude session/quota** still gates long batches. Classified `FAILED_QUOTA` with abort-fast (no repair spin), but a quota-exhausted run still fails. Operational.
3. **Pseudo-component decomposition** (out of scope, unchanged): non-domain objects slugify features into "components" (e.g. `keyway_in_the_bore`) that aren't buildable solids → N-1/N, blocks assembly. Only `quadcopter drone` has a real domain manifest.
4. **Generic placement is structural, not physical** — non-domain objects get a grid layout (valid, passes gate; not physically arranged). Domain placement rules exist only for drone.
5. **STEPs composed as-is** — no inter-component boolean fusing / interference resolution; parts placed, not merged. Fine for concept CAD; matters for manufacturing/URDF.

---

## Recommended next milestones

1. **Component-level turn robustness** — make individual component calls converge in ≤5–7 turns reliably (e.g. discourage Bash self-testing; tighten the per-component prompt). Closes the last `error_max_turns` surface. *(prompt change — was deferred this phase)*
2. **Domain manifests + placement rules beyond drone** — add real component graphs for the high-value classes (gripper, gimbal, gearbox, RC chassis, arm) to fix pseudo-component decomposition and give physical placement. Biggest pass-rate lever for the full 10-object benchmark.
3. **Quota-aware batching** — pause/resume on `FAILED_QUOTA`, checkpoint, auto-retry after reset; surface remaining budget.
4. **Inter-component fit** — optional boolean fuse / interference check in the composer for manufacturability.
5. **URDF/SRDF from `assembly_graph.json`** — MVP v2: the graph already carries node ids, parents, and placement transforms; emit links/joints from it.
6. **Full 10-object benchmark rerun** on a fresh quota window to confirm the refactor's gains generalize.

---

*Checkpoint only. No new implementation started. Frontend, Qwen prompts, component prompts, and CAD modeling quality unchanged this phase.*
