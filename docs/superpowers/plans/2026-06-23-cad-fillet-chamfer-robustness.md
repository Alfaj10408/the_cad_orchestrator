# CAD Fillet/Chamfer Robustness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Stop component-level fillet/chamfer failures (chamfer on edges consumed by a prior global fillet / curved edges) by hardening the per-component code-writer prompt (prevention + mandatory fallback), adding a targeted repair hint, and a planning-level note.

**Architecture:** Edit two text/prompt producers: `component_validator.component_prompt()` (prevention rules + mandatory try/except→base-solid fallback) and `component_validator._repair_hint()` (new edge-suitability branch), plus a one-line note in `worker_prompt_system.txt`. Both `component_prompt()` and `_repair_hint()` are pure string functions → unit-tested deterministically. Final acceptance is live CAD reruns.

**Tech Stack:** Python 3.11 CAD env (`/root/anaconda3/envs/cadskills/bin/python`), CAD invocation prefix `LD_PRELOAD=/root/anaconda3/envs/cadskills/lib/libexpat.so.1`, build123d/OCC, pytest.

## Global Constraints
- **Scope ONLY:** `backend/app/orchestrator/component_validator.py` and `backend/app/ai/prompts/worker_prompt_system.txt`. Tests under product-root `tests/`.
- **DO NOT change:** API, multi-worker, queueing, persistence, hardening, assembly pipeline, `cad_runner`, `claude_generation.py`. STOP/BLOCKED if a task seems to need them.
- **Engine-freeze guard WILL become non-empty for `component_validator.py`** — this is accepted for this milestone (the file is the only home of the code-writer prompt + repair hints). No other `services/`/`orchestrator/` file changes. After validation, **re-baseline the CAD-engine tag** (e.g. `v0.3.1`).
- Fallback is **prompt-mandated** (Claude writes the try/except) — NOT a Python wrapper around generated code. No new dependency. No schema. No CAD logic change.
- Run from product root with the cadskills python.

---

## Task 1 — prevention rules + mandatory fallback in `component_prompt()`

**Files:**
- Modify: `backend/app/orchestrator/component_validator.py` (`component_prompt`)
- Test: `tests/test_component_prompt_robustness.py`

**Interfaces — Produces:** `component_prompt(design_spec, comp)` output now contains explicit fillet/chamfer prevention rules + a mandatory try/except→base-solid fallback example. Signature unchanged.

- [ ] **Step 1: Write the failing test**
```python
# tests/test_component_prompt_robustness.py
import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
from app.orchestrator import component_validator as cv

_SPEC = {"object_class": "airplane"}
_COMP = {"name": "internal_cavity_weight_relief", "role": "weight relief",
         "source": "output/components/icwr/generate.py",
         "step": "output/components/icwr/icwr.step",
         "target_bbox_mm": {"x": 40, "y": 30, "z": 20}}


def test_prompt_marks_fillet_chamfer_cosmetic():
    p = cv.component_prompt(_SPEC, _COMP).lower()
    assert "cosmetic" in p
    # valid solid required WITHOUT fillets/chamfers
    assert "without" in p and "fillet" in p


def test_prompt_forbids_same_edge_and_orders_chamfer_first():
    p = cv.component_prompt(_SPEC, _COMP).lower()
    assert "same edge" in p
    assert "chamfer before fillet" in p


def test_prompt_requires_narrow_selection_and_straight_edges():
    p = cv.component_prompt(_SPEC, _COMP)
    assert "filter_by" in p                  # narrow selection guidance
    assert "GeomType.LINE" in p              # straight-edges-only for chamfer


def test_prompt_mandates_try_except_base_fallback():
    p = cv.component_prompt(_SPEC, _COMP)
    assert "try:" in p and "except" in p
    low = p.lower()
    assert "base" in low and ("keep" in low or "fall back" in low or "continue" in low)
    # gen_step must always return a valid solid even if cosmetics fail
    assert "always return" in low or "must always" in low
```

- [ ] **Step 2: Run, verify fail** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_component_prompt_robustness.py -v`
Expected: FAIL (current prompt lacks these directives).

- [ ] **Step 3: Implement** — in `backend/app/orchestrator/component_validator.py`, edit `component_prompt()`. Replace the single requirement line:
```python
- Closed, positive-volume solid; manufacturable; fillets/chamfers where natural.
```
with an expanded fillet/chamfer block (keep all other requirement lines unchanged):
```python
- Closed, positive-volume solid; manufacturable.
- Fillets and chamfers are COSMETIC: the component MUST be a valid closed
  positive-volume solid WITHOUT any fillet/chamfer. Add them only as a finishing
  touch, following these rules:
  * Never apply fillet() and chamfer() to the SAME edge or overlapping edge sets.
  * If both are needed on nearby edges, do the CHAMFER BEFORE the FILLET (chamfer
    needs the original straight edge; fillet the remaining straight edges after).
  * Select a NARROW, specific edge set for each cosmetic op — never the global
    part.edges(); use e.g. .edges().filter_by(Axis.Z) or one face's edges.
  * Chamfer only STRAIGHT edges: .edges().filter_by(GeomType.LINE). Never chamfer
    curved or already-filleted edges.
  * Keep radius/length small relative to the local wall; prefer max_fillet().
  * Build the valid solid FIRST, then wrap EACH cosmetic fillet/chamfer in its own
    try/except; on ANY exception keep the pre-cosmetic solid and continue, e.g.:
        base = part
        try:
            part = fillet(part.edges().filter_by(Axis.Z), radius=r)
        except Exception:
            part = base   # cosmetic failed -> keep the valid base solid
    gen_step() MUST always return a valid positive-volume solid even if every
    cosmetic op fails.
```

- [ ] **Step 4: Run, verify pass** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_component_prompt_robustness.py -v` → all pass.

- [ ] **Step 5: Commit**
```bash
cd /root/all_project_models/alfaj/text-to-cad-product
git add backend/app/orchestrator/component_validator.py tests/test_component_prompt_robustness.py
git commit -m "fix(cad): component_prompt fillet/chamfer prevention rules + mandatory base-solid fallback"
```

**Success:** prompt marks fillet/chamfer cosmetic + valid-without; forbids same-edge; orders chamfer-before-fillet; mandates narrow `filter_by` selection + `GeomType.LINE` for chamfer; embeds try/except→base fallback with "always return a valid solid".

---

## Task 2 — new edge-suitability repair hint in `_repair_hint()`

**Files:**
- Modify: `backend/app/orchestrator/component_validator.py` (`_repair_hint`)
- Test: `tests/test_repair_hint_edges.py`

**Interfaces — Consumes:** existing `_repair_hint(reason: str) -> str`. **Produces:** a new branch returning edge-suitability guidance, placed BEFORE the generic `chamfer` branch so it wins for edge-consumed/curved-edge failures.

- [ ] **Step 1: Write the failing test**
```python
# tests/test_repair_hint_edges.py
import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
from app.orchestrator import component_validator as cv


def test_chamfer_on_consumed_edge_gets_edge_hint():
    reason = "BRep_API: chamfer failed, no faces for edge (edge not found)"
    h = cv._repair_hint(reason).lower()
    assert "no longer exist" in h or "curved" in h
    assert "chamfer before the fillet" in h
    assert "geomtype.line" in h


def test_fillet_edge_failure_gets_edge_hint():
    reason = "Standard_NullObject: fillet on edge produced null shape"
    h = cv._repair_hint(reason).lower()
    assert "try/except" in h and "base solid" in h


def test_plain_chamfer_length_still_gets_length_hint():
    # a chamfer-length complaint WITHOUT edge keywords keeps the existing hint
    reason = "chamfer length too large for the face"
    h = cv._repair_hint(reason).lower()
    assert "length" in h and "reduce" in h


def test_fillet_radius_branch_unchanged():
    reason = "fillet radius exceeds max_fillet for this geometry"
    h = cv._repair_hint(reason).lower()
    assert "radius" in h


def test_unrelated_reason_no_hint():
    assert cv._repair_hint("disk full") == ""
```

- [ ] **Step 2: Run, verify fail** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_repair_hint_edges.py -v`
Expected: FAIL (edge-suitability branch not present; consumed-edge reason currently falls into the generic chamfer/length hint).

- [ ] **Step 3: Implement** — in `_repair_hint()`, add the edge-suitability branch. Insert it AFTER the `fillet ... radius` branch and BEFORE the generic `if "chamfer" in r:` branch:
```python
    if (("chamfer" in r or "fillet" in r) and
            ("edge" in r or "no faces" in r or "brep" in r or "standard_" in r
             or "null" in r or "not found" in r)):
        return ("\nHINT: this fillet/chamfer targets edges that no longer exist or are "
                "curved (often because a prior global fillet already rounded them). "
                "Fixes, in order: (1) wrap the cosmetic op in try/except and keep the "
                "base solid on failure; (2) if fillet and chamfer share edges, do the "
                "chamfer before the fillet; (3) select a narrow, specific edge set and "
                "chamfer only straight edges (.filter_by(GeomType.LINE)); (4) if still "
                "failing, remove the cosmetic op — the component is valid without it.\n")
```
(Leave the existing fillet-radius, generic chamfer-length, API-signature, and degenerate branches unchanged; this new branch only catches reasons that ALSO mention an edge/OCC keyword.)

- [ ] **Step 4: Run, verify pass** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_repair_hint_edges.py -v` → all pass.

- [ ] **Step 5: Regression — existing repair/validator behavior** —
```bash
cd /root/all_project_models/alfaj/text-to-cad-product
/root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_component_prompt_robustness.py tests/test_repair_hint_edges.py -q
```
Expected: all pass (new branch is additive; plain length/radius reasons still route to their existing hints).

- [ ] **Step 6: Commit**
```bash
git add backend/app/orchestrator/component_validator.py tests/test_repair_hint_edges.py
git commit -m "fix(cad): edge-suitability repair hint for chamfer/fillet on consumed/curved edges"
```

**Success:** consumed/curved-edge chamfer/fillet reasons get the new edge hint (chamfer-before-fillet, GeomType.LINE, try/except→base, remove-if-still-failing); plain length/radius reasons keep their existing hints; unrelated reasons → "".

---

## Task 3 — planning-level reinforcement note in `worker_prompt_system.txt`

**Files:**
- Modify: `backend/app/ai/prompts/worker_prompt_system.txt` (NOT frozen)
- Test: `tests/test_worker_prompt_note.py`

**Interfaces — Produces:** the planning prompt contains a clause treating fillets/chamfers as cosmetic, never both on the same edge, small + local.

- [ ] **Step 1: Write the failing test**
```python
# tests/test_worker_prompt_note.py
import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
_PROMPT = _BACKEND / "app" / "ai" / "prompts" / "worker_prompt_system.txt"


def test_planning_prompt_has_cosmetic_fillet_note():
    text = _PROMPT.read_text().lower()
    assert "cosmetic" in text
    assert "same edge" in text
    assert "small" in text and "local" in text
```

- [ ] **Step 2: Run, verify fail** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_worker_prompt_note.py -v`
Expected: FAIL (clause absent).

- [ ] **Step 3: Implement** — in `backend/app/ai/prompts/worker_prompt_system.txt`, change the geometry-approach line:
```
- Geometry approach: primitives + boolean ops + fillets/chamfers per feature.
```
to:
```
- Geometry approach: primitives + boolean ops + fillets/chamfers per feature.
  Treat fillets/chamfers as cosmetic: never call for both on the same edge, and
  keep them small and local.
```

- [ ] **Step 4: Run, verify pass** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_worker_prompt_note.py -v` → pass.

- [ ] **Step 5: Commit**
```bash
git add backend/app/ai/prompts/worker_prompt_system.txt tests/test_worker_prompt_note.py
git commit -m "fix(cad): planning prompt note — fillets/chamfers cosmetic, never same edge, small+local"
```

**Success:** planning prompt carries the cosmetic / same-edge / small+local clause; test passes.

---

## Task 4 — live verification + engine re-baseline

**Files:** none (operator verification + tagging). The pure-function tests are green from T1–T3.

**Acknowledgement:** the engine-freeze guard is now NON-EMPTY for `component_validator.py` (accepted); a new CAD-engine baseline tag is required after validation passes.

- [ ] **Step 1: Confirm pure-function suite + guard delta is scoped** —
```bash
cd /root/all_project_models/alfaj/text-to-cad-product
/root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_component_prompt_robustness.py tests/test_repair_hint_edges.py tests/test_worker_prompt_note.py -q
git diff --stat v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator
```
Expected: tests pass; the guard diff lists ONLY `backend/app/orchestrator/component_validator.py` (no other engine file). If any other `services/`/`orchestrator/` file appears → STOP (scope violation).

- [ ] **Step 2: Rerun the failed component in isolation** — using the CAD env + prefix, exercise `internal_cavity_weight_relief` alone (regenerate via the orchestrator component path / the project's component harness against the airplane brief). Confirm: STEP export succeeds and inspect reports a single positive-volume solid; if a cosmetic op fails it now degrades to the base solid rather than failing the component.
Command shape (CAD env):
```bash
cd /root/all_project_models/alfaj/text-to-cad-product
LD_PRELOAD=/root/anaconda3/envs/cadskills/lib/libexpat.so.1 \
  /root/anaconda3/envs/cadskills/bin/python <component-rerun harness for internal_cavity_weight_relief>
```
Expected: component validates (`valid: true`).

- [ ] **Step 3: Rerun the airplane job end-to-end** — submit/regenerate the full airplane job through the normal generation path (this is the explicitly-authorized long CAD run). Confirm every component passes and the assembly builds.
Expected: all components `valid`; assembly produced.

- [ ] **Step 4: Validate final artifacts** — confirm the airplane project produced and each opens/parses:
  - **STEP** present + inspects to a valid solid.
  - **STL** present + non-empty mesh.
  - **GLB** present.
  - **Preview opens** (GLB viewer renders the model).

- [ ] **Step 5: Re-baseline the CAD-engine tag** — once Steps 2–4 pass, tag the new engine baseline so future freeze-guard checks compare against it:
```bash
cd /root/all_project_models/alfaj/text-to-cad-product
git tag -a v0.3.1-cad-fillet-chamfer-robustness -m "CAD fillet/chamfer robustness — new engine baseline
- component_prompt: fillet/chamfer prevention + mandatory base-solid fallback
- _repair_hint: edge-suitability branch (consumed/curved edges)
- planning prompt cosmetic note
- airplane job validated end-to-end (STEP/STL/GLB + preview)"
```
(Push only on explicit instruction.)

**Success:** failed component validates; airplane job completes; final STEP/STL/GLB present + preview opens; guard delta limited to `component_validator.py`; new engine baseline tag created.

---

## Self-review
**Spec coverage:** prevention rules in `component_prompt()` — cosmetic/valid-without, never-same-edge, chamfer-before-fillet, narrow `filter_by`, `GeomType.LINE`, conservative size (T1) ✓; mandatory try/except→base-solid fallback with "always return valid solid" (T1) ✓; new edge-suitability `_repair_hint()` branch placed before generic chamfer branch, existing branches preserved (T2) ✓; planning-level note (T3) ✓; verification = failed-component rerun + airplane rerun + STEP + STL + GLB + preview (T4) ✓; freeze-guard-non-empty acknowledged + new baseline tag (T4) ✓; scope limited to the two files, no API/multi-worker/queue/persistence/hardening/assembly/cad_runner/claude_generation changes (Global Constraints + T4 Step 1 guard-scope check) ✓.
**Placeholder scan:** none in code steps. T4 Steps 2–3 reference "<component-rerun harness>" / "normal generation path" as operator actions because the live job trigger depends on the running stack — these are verification commands, not code to author; acceptance criteria are concrete.
**Type consistency:** `component_prompt(design_spec, comp)` and `_repair_hint(reason)->str` signatures unchanged across tasks; tests import `app.orchestrator.component_validator as cv`; new hint branch returns a string like the others; no cross-task symbol drift.
