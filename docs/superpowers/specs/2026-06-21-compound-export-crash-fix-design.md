# Spec — Compound(children) Export-Crash Fix (Option D)

**Date:** 2026-06-21
**Status:** Approved design → implementation
**Scope:** Make per-component STEP export robust to a *post-export* crash. One function (`run_component`). Geometry validation is the source of truth.

## Problem (evidence)
A generated component `gen_step()` returning `Compound(children=[...])` makes the cadpy `step` CLI **SIGSEGV (rc=139 / Python returncode -11), deterministically** (6/6). The `.step` is **fully written (58.5 KB) before** the crash (segfault in a post-export GLB/cleanup stage). `run_component` fails solely because `returncode != 0`, discarding a valid STEP; native crash → empty stderr → bare "STEP export failed" → no repair convergence. Assembly is immune because `assembly_composer` emits OCC `TopoDS_Builder` compounds, never `Compound(children=...)`.

## Fix (Option D — step-export layer, validate-by-geometry)
In `run_component`, treat a **crash-class** nonzero exit as non-fatal *if a STEP was still produced and inspects to a valid solid*. Geometry (inspection + the existing `validate_component` gate) is the arbiter, not the crashy exit code.

New logic:
- `crash_exit = step.returncode is not None and (step.returncode < 0 or step.returncode >= 128)` (Python returns negative for signals; `>=128` defensive).
- Fail immediately (unchanged) when: STEP file missing, **or** nonzero exit that is **not** crash-class (ordinary tool error, e.g. rc=1).
- On a crash-class exit **with** a STEP present: proceed to inspection. Accept (`ok=True`, return facts) only if inspection yields facts; if inspection cannot read a valid solid (truncated STEP), reject with a descriptive reason including the crash returncode.
- `returncode == 0` path is **unchanged** (incl. the existing `ok=True, facts=None` → `validate_component` "no inspection facts" behavior).

Helper `_is_crash_exit(returncode) -> bool` factored out (pure, unit-testable).

## Requirements honored
- Geometry validation = source of truth ✓.
- Accept only when STEP exists AND inspection passes AND (downstream) geometry validation passes ✓.
- Restricted to crash-class exits (signal / rc≥128) — ordinary nonzero still fails ✓.
- Preserve: prompts, turn budgets, repair logic, assembly, metrics, benchmark harness ✓ (only `run_component`'s export-acceptance branch changes).

## Non-goals
No prompt change; no cadpy/`step` CLI change; no assembly/frontend/API change; no source rewriting/sanitizing.

## Risk
Accepting output from a crashed process. Mitigation: the existing inspect + `validate_component` gate (`shapeCount≥1`, non-degenerate bounds) rejects truncated/empty STEPs; inspection runs on the written BREP `.step` (not the crashy in-memory Compound), so it is safe. Fails safe when the crash precedes a complete STEP write.

## Verification
1. **Pure unit:** `_is_crash_exit`: -11→True, 139→True, 1→False, 0→False, None→False.
2. **Synthetic reproducer (live CAD, deterministic):** `Compound(children=[...])` source → `run_component` → `ok=True`, facts `shapeCount≥1`; and a guaranteed-empty/truncated case → `ok=False`.
3. **four_wheels re-run:** `run_component` on the chassis wheel source → valid, first attempt, 0 repairs.
4. **RC chassis re-run:** full `claude_generation.run()` → 8/8, assembly valid, COMPLETED.
5. **Resume benchmark:** objects 8→9→10; full 10-object table + comparison.
