# Error-Aware Repair Hints — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Append build123d-specific remediation hints to the component repair prompt so known geometry/API errors (esp. fillet) converge within the existing 2-repair budget.

**Architecture:** One pure helper `_repair_hint(reason)` + one-line wiring into `repair_prompt`. No other behavior changes.

**Tech Stack:** Python 3.11 (`/root/anaconda3/envs/cadskills/bin/python`), pytest.

## Global Constraints
- Only `backend/app/orchestrator/component_validator.py` changes (`repair_prompt` + new `_repair_hint`).
- Preserve: `CLAUDE_CODE_MAX_REPAIRS=2` (do NOT raise), component tools `Read,Write,Edit`, `max_turns=12`, anti-exploration `component_prompt`, metrics, deterministic assembly, failure classes.
- No Qwen/component-generation prompt, assembly, frontend, API changes.
- Tests at product-root `tests/`; run from product root. Git: product-root repo; commit each task.

---

## Task 1 — `_repair_hint` + wire into `repair_prompt`

**Files:** Modify `backend/app/orchestrator/component_validator.py`; Test: `tests/test_repair_hints.py` (new).

**Interfaces — Produces:** `_repair_hint(reason: str) -> str`; `repair_prompt(comp, reason)` unchanged signature, now appends the hint.

- [ ] **Step 1: Write the failing test**
```python
# tests/test_repair_hints.py
import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
from app.orchestrator import component_validator as cv

_COMP = {"name": "arm"}

def test_fillet_hint():
    r = cv.repair_prompt(_COMP, "FilletError: creating a fillet with radius of 0.2, try a smaller value or use max_fillet()")
    low = r.lower()
    assert "max_fillet" in low and "reduce" in low
    assert "fillet with radius of 0.2" in r          # original error preserved verbatim
    assert "edit tool" in low                          # generic Edit instruction preserved

def test_chamfer_hint():
    assert "chamfer" in cv.repair_prompt(_COMP, "ChamferError: chamfer length too large").lower()

def test_api_hint():
    low = cv.repair_prompt(_COMP, "NameError: name 'translate' is not defined").lower()
    assert "location" in low and "translate()" in low

def test_empty_solid_hint():
    low = cv.repair_prompt(_COMP, "degenerate/empty (shapes=0, bounds={})").lower()
    assert "positive-volume" in low

def test_unknown_error_no_hint():
    # unknown error -> no HINT line, but generic Edit instruction + error remain
    r = cv.repair_prompt(_COMP, "some unexpected failure xyz")
    assert "HINT:" not in r
    assert "some unexpected failure xyz" in r
    assert "Edit tool" in r

def test_repair_hint_unknown_returns_empty():
    assert cv._repair_hint("totally unrelated message") == ""
```

- [ ] **Step 2: Run, verify it fails**

Run: `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_repair_hints.py -v`
Expected: FAIL (`_repair_hint` not defined; hints absent).

- [ ] **Step 3: Implement**

In `component_validator.py`, add `_repair_hint` and append it in `repair_prompt`:
```python
def _repair_hint(reason: str) -> str:
    r = (reason or "").lower()
    if "fillet" in r and ("radius" in r or "max_fillet" in r):
        return ("\nHINT: the fillet radius is too large for this geometry. Substantially "
                "reduce it, use max_fillet() to compute a safe radius, or remove the fillet "
                "on those edges.\n")
    if "chamfer" in r:
        return "\nHINT: the chamfer length is too large. Reduce it or remove the chamfer.\n"
    if "not defined" in r or "nameerror" in r or "attributeerror" in r:
        return ("\nHINT: use valid build123d API only — move solids with .moved(Location(...)) "
                "or Pos(...), place sketch geometry with Locations(...); do not call translate() "
                "or any undefined name.\n")
    if "degenerate" in r or "no solid" in r or "empty" in r:
        return ("\nHINT: the result has no positive-volume solid. Ensure boolean ops do not "
                "remove all material and that all dimensions are > 0.\n")
    return ""


def repair_prompt(comp: dict, reason: str) -> str:
    return (
        f"\n\n--- REWRITE REQUIRED (component {comp['name']}) ---\n{reason}\n"
        "Use the Edit tool to change ONLY the failing line/section identified by "
        "the error above. Do not rewrite the whole file. Do not execute or test. "
        "Make the smallest fix that yields a single closed positive-volume solid; "
        "the backend re-validates.\n"
        + _repair_hint(reason)
    )
```

- [ ] **Step 4: Run, verify it passes** — same command → PASS.

- [ ] **Step 5: Regression + commit**

Run: `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_component_robustness.py tests/test_repair_hints.py -q` → all pass.
```bash
git add backend/app/orchestrator/component_validator.py tests/test_repair_hints.py
git commit -m "feat(repair): error-aware remediation hints (fillet/chamfer/API/empty)"
```

**Tests/verification:** unit above + component_robustness regression.
**Rollback strategy:** revert commit; `repair_prompt` reverts to generic (the `+ _repair_hint(reason)` is the only behavioral add).
**Success criteria:** correct hint per error class; unknown → no HINT line; error text + Edit instruction preserved.

---

## Task 2 — Verification

> Real Claude + CAD. Clean up scratch projects after.

### 2.1 Unit tests (all)
- [ ] Run: `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/ -q`. **Success:** all pass.

### 2.2 Decisive repair-path test (live)
- [ ] Seed an `arm` source whose `gen_step()` calls `fillet(<thin edges>, 0.2)` and errors on STEP export. Run ONE component repair iteration (component_prompt + repair_prompt with the captured fillet error, tools=`Read,Write,Edit`, max_turns=12). Confirm: Claude's Edit reduces/removes/`max_fillet()`-s the fillet (Bash off), and the component then validates. **Success:** recovers within budget; contrast the prior 3-attempt non-convergence.

### 2.3 Drone re-run (the failed object, live)
- [ ] Run a full drone generation through `claude_generation.run()`. Confirm COMPLETED, 8/8 components (arm now recovers if it trips the fillet), assembly valid; read `component_metrics.json`. **Success:** drone COMPLETES. (Note: arm geometry is nondeterministic; success is expected but variance possible — record metrics either way.)

### 2.4 Report
- [ ] Append a short note to `benchmark_summary.md`: the fix, the 2.2 before/after convergence evidence, and the 2.3 drone result. **Success:** committed.

---

## Self-review
**Spec coverage:** `_repair_hint` with fillet/chamfer/API/empty classes + generic fallback (Task 1) ✓; appended in `repair_prompt`, error preserved (Task 1) ✓; preserved items untouched (Global Constraints) ✓; verification incl. decisive live repair + drone rerun (Task 2) ✓.
**Placeholder scan:** none — full code in each step.
**Type consistency:** `_repair_hint(reason: str) -> str`; `repair_prompt(comp, reason)` signature unchanged.
