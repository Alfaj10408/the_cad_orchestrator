# Compound(children) Export-Crash Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make `run_component` accept a STEP that exports validly but whose CLI process then crashes (crash-class exit), using geometry inspection as the source of truth.

**Architecture:** Add a pure `_is_crash_exit(returncode)` predicate and rework `run_component`'s export-acceptance branch. No other code changes.

**Tech Stack:** Python 3.11 (`/root/anaconda3/envs/cadskills/bin/python`), pytest, build123d, cadpy `step`/`inspect` (via `cad_runner`).

## Global Constraints
- Only `backend/app/orchestrator/component_validator.py` (`run_component` + new `_is_crash_exit`) changes.
- Preserve: prompts, turn budgets, repair logic, assembly, metrics, benchmark harness. The `returncode==0` path of `run_component` is unchanged.
- Crash-class = `returncode is not None and (returncode < 0 or returncode >= 128)`.
- Tests at product-root `tests/`; run from product root. Git: product-root repo; commit each task.

---

## Task 1 — `_is_crash_exit` + robust `run_component`

**Files:** Modify `backend/app/orchestrator/component_validator.py`; Test: `tests/test_export_crash.py` (new).

**Interfaces — Produces:** `_is_crash_exit(returncode: int | None) -> bool`; `run_component` unchanged signature, new acceptance branch.

- [ ] **Step 1: Write the failing test (pure predicate)**
```python
# tests/test_export_crash.py
import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
from app.orchestrator import component_validator as cv

def test_is_crash_exit():
    assert cv._is_crash_exit(-11) is True     # SIGSEGV (Python signal form)
    assert cv._is_crash_exit(139) is True      # 128+11 (shell form)
    assert cv._is_crash_exit(134) is True      # SIGABRT
    assert cv._is_crash_exit(1) is False       # ordinary tool error
    assert cv._is_crash_exit(0) is False
    assert cv._is_crash_exit(None) is False
```

- [ ] **Step 2: Run, verify it fails**

Run: `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_export_crash.py -v`
Expected: FAIL (`_is_crash_exit` not defined).

- [ ] **Step 3: Implement**

Add the predicate and rework the export branch in `run_component`:
```python
def _is_crash_exit(returncode) -> bool:
    """True for signal/crash-class exits (Python signals are negative; shells
    report 128+signal). Ordinary nonzero tool errors are NOT crash-class."""
    return returncode is not None and (returncode < 0 or returncode >= 128)
```
Replace the current export check:
```python
    step = cad_runner._run(cad_runner.STEP_TOOL, [f"{src_rel}={step_rel}"], cwd=ws)
    if step.returncode != 0 or not (ws / step_rel).exists():
        return {"ok": False, "reason": (step.stderr or "STEP export failed")[-800:], "facts": None}
```
with:
```python
    step = cad_runner._run(cad_runner.STEP_TOOL, [f"{src_rel}={step_rel}"], cwd=ws)
    step_exists = (ws / step_rel).exists()
    crash = _is_crash_exit(step.returncode)
    # Geometry is the source of truth: a crash-class exit (e.g. a post-export
    # SIGSEGV in GLB/cleanup) is tolerated IF a STEP was produced and inspects
    # to a valid solid. Ordinary nonzero exits and missing STEPs still fail.
    if not step_exists or (step.returncode != 0 and not crash):
        return {"ok": False, "reason": (step.stderr or "STEP export failed")[-800:], "facts": None}
```
Then, after computing `facts` from inspection, reject a crash-class export that produced no inspectable solid (leave the `returncode==0` path returning `ok=True` even when facts is None, as today):
```python
    ref = step_rel.rsplit(".", 1)[0]
    insp = cad_runner._run(
        cad_runner.INSPECT_TOOL, ["refs", "--facts", f"@cad[{ref}]"], cwd=ws
    )
    facts = None
    try:
        facts = json.loads(insp.stdout)["tokens"][0]["summary"]
    except Exception:  # noqa: BLE001
        pass
    if crash and facts is None:
        return {"ok": False,
                "reason": f"STEP export crashed (rc={step.returncode}) and produced no inspectable solid",
                "facts": None}
    return {"ok": True, "reason": None, "facts": facts}
```

- [ ] **Step 4: Run, verify pure test passes** — same command → PASS.

- [ ] **Step 5: Regression + commit**

Run: `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_component_robustness.py tests/test_repair_hints.py tests/test_export_crash.py -q` → all pass.
```bash
git add backend/app/orchestrator/component_validator.py tests/test_export_crash.py
git commit -m "feat(export): accept valid STEP on crash-class exit (geometry is source of truth)"
```

**Tests/verification:** pure unit above + regression.
**Rollback strategy:** revert commit; `run_component` reverts to fail-on-any-nonzero.
**Success criteria:** `_is_crash_exit` correct; `run_component` accepts crash-class exits with a valid STEP, still fails on ordinary nonzero / missing / uninspectable STEP; `returncode==0` path unchanged.

---

## Task 2 — Verification

> Real CAD + Claude. Clean up scratch after each.

### 2.1 Synthetic reproducer (live, deterministic)
- [ ] Write a `Compound(children=[...])` component source (e.g. the 4-wheel source) into a scratch workspace; call `run_component` directly. Confirm: step CLI returns crash-class rc, `.step` produced, `run_component` returns `ok=True` with facts `shapeCount≥1`. Then a guaranteed-bad case (e.g. empty `gen_step()` / no solid) → `ok=False`.
- **Success:** crash-with-valid-STEP accepted; no-solid rejected.

### 2.2 four_wheels re-run
- [ ] Run the chassis `four_wheels` source through `run_component` → valid (multi-solid) first attempt, 0 repairs.
- **Success:** valid.

### 2.3 RC car chassis re-run
- [ ] Full `claude_generation.run()` for "RC car chassis" → 8/8 components, assembly valid, COMPLETED; read `component_metrics.json`.
- **Success:** COMPLETED.

### 2.4 Resume benchmark
- [ ] Run objects 8→9→10 (RC chassis, robotic arm, desktop CNC frame) with halt-on-failure. Combine with objects 1–7 for the full 10-object table; update `benchmark_summary.md` (pass rate, turns/comp, repairs, comparison vs the 2026-06-19 10-object run).
- **Success:** report committed; ideally 10/10 (or halt + root-cause on any new failure).

---

## Self-review
**Spec coverage:** geometry-as-truth acceptance on crash-class exit (Task 1) ✓; STEP-exists + inspection-passes gating (Task 1) ✓; crash-class restriction via `_is_crash_exit` (Task 1) ✓; preserved items untouched (Global Constraints) ✓; verification: reproducer, four_wheels, chassis, resume benchmark (Task 2) ✓.
**Placeholder scan:** none.
**Type consistency:** `_is_crash_exit(returncode)->bool`; `run_component` signature unchanged; return dict shape unchanged (`ok`/`reason`/`facts`).
