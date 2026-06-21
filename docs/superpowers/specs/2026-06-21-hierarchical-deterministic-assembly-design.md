# Spec — Hierarchical Deterministic Assembly

**Date:** 2026-06-21
**Status:** Approved design → implementation
**Scope:** Eliminate monolithic Claude calls in the `qwen_claude_code` pipeline by replacing whole-assembly generation (and whole-object single-shot generation) with a deterministic, Claude-free assembly process operating on validated component STEP files.

---

## 1. Problem

Benchmark (2026-06-19, `benchmark_summary.md`) found:
- Component generation is strong: **46/48** components valid on non-quota runs.
- End-to-end object pass = **1/10**. Failures cluster at **monolithic Claude calls**:
  - **Single-shot whole-object generation** (simple parts) — one large Claude call hits `max_turns=15` (`subtype:error_max_turns`, `num_turns:16` → `is_error` → FAILED).
  - **Whole-assembly generation** — `assembly_builder.assembly_prompt()` produces one large Claude call that re-creates all components AND places them in `output/generate.py`; same `max_turns` failure (`"assembly invalid: claude exit 1, is_error=True"`).
- Secondary: **quota exhaustion** (`"You've hit your session limit"`) was treated identically to a CAD failure and spun the full repair budget (24 wasted attempts/object).

Root cause: any stage that depends on a single large Claude call exceeds the turn ceiling. Per-component calls (small) succeed.

## 2. Goals

- **No single Claude call needs more than ~5–7 turns.** Achieved by: keeping only per-component generation as Claude calls; making assembly fully deterministic (0 Claude calls).
- Route **all** objects (simple and complex) through one hierarchical path. Remove the single-shot branch.
- Assembly composes **exclusively** the already-validated component STEP files (no re-execution of component `gen_step()`).
- Persist `reports/assembly_graph.json` as a first-class artifact (foundation for future URDF/SRDF).
- Distinguish failure classes: `FAILED_CAD`, `FAILED_QUOTA`, `FAILED_TURNS`; abort fast on quota.

## 3. Non-goals (explicitly unchanged)

Qwen planning/prompts, component-generation prompts, component repair loop, CAD modeling quality, decomposition logic (`component_planner` manifests), frontend. No improvement to *what* CAD a component contains — only how components are composed into an assembly.

## 4. Architecture

### 4.1 New pipeline (every object)

```
design_spec + component_manifest          (Qwen — unchanged)
  → per-component generation               (small Claude call, ONE part each)  ← only Claude
  → per-component validation                (STEP export + inspect + gate — unchanged)
  → assembly graph        [NEW deterministic]   → reports/assembly_graph.json
  → placement resolution  [NEW deterministic, 0 Claude]
  → assembly compose+export [NEW deterministic]  (STEP import + transforms)
  → assembly validation     (existing anti-primitive gate + new structural checks)
  → artifacts
```

Simple parts (`component_count < 2`) are treated as a **1-node graph**: one small component call, then deterministic single-part "assembly" (import the one validated STEP, identity transform, export). The single-shot branch in `claude_generation.py` is removed.

### 4.2 New modules (isolated, pure where possible)

| Module (new) | Purpose | Inputs | Output | Claude |
|---|---|---|---|---|
| `app/orchestrator/assembly_graph.py` | Expand manifest into an instance graph; expand quantities (`arm×4 → arm_0..3`); assign parent frame + role | `manifest`, `design_spec` | graph dict | no |
| `app/orchestrator/placement_rules.py` | Map (role, instance index, bbox, envelope) → `Location` transform; domain rules + generic fallback layout | graph, design_spec | transforms per node | no |
| `app/orchestrator/assembly_composer.py` | Emit a machine-written `output/generate.py` that imports each validated component STEP, applies its transform, builds a labeled `Compound`; run existing CAD export | graph+transforms, component STEP paths | `generate.py` + STEP/STL/GLB/snapshot | no |

| Module (modified) | Change |
|---|---|
| `app/orchestrator/assembly_builder.py` | Keep `validate_assembly` (extended, §8). **Remove** `assembly_prompt` + `repair_prompt` (no Claude assembly). |
| `app/services/claude_generation.py` | Replace assembly loop (lines ~326–367) with deterministic composer call. Route simple parts through the 1-node graph. Remove single-shot branch (~369–415). Add failure-class mapping (§7). |
| `app/services/claude_code_adapter.py` | `run_claude` returns a `failure_class` field (§7 detection). |

## 5. Data structure — `reports/assembly_graph.json`

First-class persisted artifact. Schema:

```json
{
  "project_id": "…",
  "object_class": "quadcopter drone assembly",
  "frame": {"id": "root", "origin_mm": [0, 0, 0], "convention": "XY base, +Z up, mm"},
  "nodes": [
    {
      "id": "arm_0",
      "component_type": "arm",
      "instance_index": 0,
      "parent": "root",
      "role": "boom arm from fuselage to a motor pod",
      "step_file": "output/components/arm/arm.step",
      "placement": {
        "translate_mm": [35.36, 35.36, 49.0],
        "rotate_deg": [0, 0, 45],
        "rule": "radial_arm"
      },
      "target_bbox_mm": {"x": 125.0, "y": 20.0, "z": 9.6}
    }
  ],
  "node_count": 17,
  "placement_engine": "domain:quadcopter_drone | generic_layout"
}
```

- `nodes[]`: every placed instance (quantities expanded). `parent` enables future kinematic trees.
- `placement`: explicit transform + the rule that produced it (traceability).
- `step_file`: the validated component STEP reused (no regeneration).
- Forward-looking: `parent` + `placement` are the seed for URDF link/joint emission later (out of scope here, but the schema reserves the structure).

## 6. Placement rules (`placement_rules.py`)

Pure function: `resolve(node, design_spec, graph) → {translate_mm, rotate_deg, rule}`.

- **Domain rule sets**, keyed by `design_spec.object_kind`. Drone (`quadcopter drone`): radial arms at 45/135/225/315°, `ARM_CENTER_R` from envelope; motor pod at arm tip; propeller atop pod; landing gear under body; camera mount under nose; battery bay / controller deck on fuselage. Encodes the working manual assembly already validated this session.
- **Generic fallback layout** (unknown `object_kind`): deterministic non-overlapping placement — pack component bounding boxes in a centered grid/stack within `overall_envelope_mm`. Produces a valid multi-solid assembly that clears the anti-primitive gate. Structural only; no claim of physical correctness.
- Quantity expansion handled in `assembly_graph` (e.g. `arm_0..3`); `placement_rules` receives `instance_index` to spread instances (e.g. angle = base + index·90°).

## 7. Failure taxonomy + quota abort-fast

`run_claude` (adapter) classifies its outcome and returns `failure_class`:

| `failure_class` | Detection (from stream-json `result` object / text) |
|---|---|
| `None` | `ok` (exit 0, not `is_error`) |
| `"quota"` | result text contains `"session limit"` / `"hit your"` / auth/usage-limit markers |
| `"turns"` | `subtype == "error_max_turns"` (or `num_turns >= max_turns` with `is_error`) |
| `"cad"` | any other `is_error` / nonzero exit |

`claude_generation` maps to job status/stage:
- `quota` → set `job.status = "FAILED_QUOTA"`, **abort the whole job immediately** — do NOT enter or continue the component repair loop (fixes the 24-attempt spin).
- `turns` → `FAILED_TURNS` (should be rare now: only a pathological single component could hit it; still surfaced).
- `cad` / validation failures → `FAILED_CAD` after the existing bounded component repair loop is exhausted.

Job statuses extended: existing `FAILED` generalized into `{FAILED_CAD, FAILED_QUOTA, FAILED_TURNS}`. (Pydantic `Job.status` is a free string today — no schema migration needed; verify before coding.) Assembly stage is Claude-free, so it can only emit `FAILED_CAD` (geometry/import/export error), never `turns`/`quota`.

## 8. Assembly validation (extended, deterministic)

Keep the anti-primitive gate (`validate_assembly`, reads `reports/inspection.txt`). Add structural checks (no Claude):
- placed instance count == `assembly_graph.node_count`.
- overall bbox within `overall_envelope_mm` × tolerance (e.g. ≤1.5×).
- every node's `step_file` existed and imported.
Write `reports/assembly_validation.json` (unchanged location) with the added fields.

## 9. Geometry composition (`assembly_composer.py`)

- Input: graph + resolved transforms + validated component STEP paths.
- Emit `output/generate.py` (machine-written, deterministic, tiny) that:
  - `from build123d import *`
  - imports each node's component STEP via build123d `import_step(<step_file>)`,
  - applies `.moved(Location(translate, rotate))`,
  - labels each (`solid.label = node.id`),
  - returns a `Compound(children=[…])` from `gen_step()`.
- Then reuse the existing deterministic CAD pipeline (`_run_cad` → `cad_runner.export_step/export_meshes/inspect_step/generate_snapshot`). No new export logic.
- Confirm during implementation: build123d `import_step` import path + composing imported solids into a `Compound` (spike test before wiring).

## 10. Artifacts

- `reports/assembly_graph.json` (NEW, first-class).
- `output/generate.py` (now machine-written, not Claude).
- `cad/model.step|stl|glb`, `cad/snapshot.png`, `reports/inspection.txt`, `reports/assembly_validation.json` (unchanged producers).

## 11. Testing

- **Unit**
  - `assembly_graph`: drone manifest → 17 nodes, quantities expanded, parents set.
  - `placement_rules`: deterministic transforms for drone roles; generic fallback yields non-overlapping bboxes within envelope.
  - `assembly_composer`: emits importable `generate.py`; references only validated STEP files.
  - failure classifier: sample `result` objects → correct `failure_class` (quota / turns / cad).
- **Integration**
  - Drone: 8 validated components → graph(17) → compose → export → validate; assert `assembly_graph.json` written, `solids≈17`, `faces>6`, bbox in envelope, **and `run_claude` NOT called during the assembly stage** (mock/spy).
  - Simple part (calibration block): 1-node graph path completes; no single-shot Claude call.
  - Regression: gear housing still passes end-to-end.
- **No-Claude assertion** is the core regression guard for the objective.

## 12. Risks / open items

- build123d STEP-import + Compose API must be confirmed (spike).
- Generic fallback layout quality is structural-only; acceptable per scope (don't improve CAD modeling).
- Drone domain rules must reproduce the previously-validated layout; port from this session's working assembly.
- Component STEPs must exist at known paths (they do, post path-fix: `output/components/{name}/{name}.step`).

## 13. Out of scope (restated)

Qwen prompts, component prompts, decomposition, CAD modeling quality, frontend, URDF/SRDF emission (schema is seeded only).
