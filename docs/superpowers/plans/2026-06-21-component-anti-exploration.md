# Component Anti-Exploration + Turn-Budget Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Recover 4/4 benchmark pass rate by stopping component-call exploratory reads and giving a modest turn-budget slack, keeping Bash-removal efficiency.

**Architecture:** Two surgical edits — rewrite the component prompt's preflight to forbid exploration and mandate Write-first (dropping the skill-read line), and bump component turn constants (8→12 cap, 6→8 near-cap). Nothing else changes.

**Tech Stack:** Python 3.11 (`/root/anaconda3/envs/cadskills/bin/python`), pytest.

## Global Constraints
- Only `backend/app/orchestrator/component_validator.py` (`component_prompt`) and `backend/app/core/config.py` change.
- Preserve: Bash off (`tools=Read,Write,Edit`), Edit-targeted `repair_prompt`, metrics (`component_metrics.json` incl. `duration_seconds`), deterministic assembly, failure classes, `write_report`.
- Do NOT touch Qwen prompts, frontend, production API, decomposition, CAD modeling quality. Do NOT raise the cap to 15.
- Tests at product-root `tests/`; run from product root `/root/all_project_models/alfaj/text-to-cad-product`. Git: product-root repo; commit each task.

---

## Task 1 — Turn budget bump (config)

**Files:** Modify `backend/app/core/config.py`; Test: `tests/test_component_robustness.py` (update existing `test_component_config_defaults`).

**Exact behavior change:** `CLAUDE_CODE_COMPONENT_MAX_TURNS` 8 → 12; `CLAUDE_CODE_COMPONENT_NEAR_CAP` 6 → 8.

- [ ] **Step 1: Update the failing test**

In `tests/test_component_robustness.py`, change the two assertions in `test_component_config_defaults`:
```python
    assert cfg.CLAUDE_CODE_COMPONENT_MAX_TURNS == 12
    assert cfg.CLAUDE_CODE_COMPONENT_NEAR_CAP == 8
```
(leave the `CLAUDE_CODE_COMPONENT_TOOLS == "Read,Write,Edit"` and the globals-unchanged assertions intact.)

- [ ] **Step 2: Run, verify it fails**

Run: `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_component_robustness.py::test_component_config_defaults -v`
Expected: FAIL (still 8 / 6).

- [ ] **Step 3: Implement**

In `config.py`, change the two defaults:
```python
CLAUDE_CODE_COMPONENT_MAX_TURNS = int(os.environ.get("CLAUDE_CODE_COMPONENT_MAX_TURNS", "12"))
CLAUDE_CODE_COMPONENT_NEAR_CAP = int(os.environ.get("CLAUDE_CODE_COMPONENT_NEAR_CAP", "8"))
```
(Leave `CLAUDE_CODE_COMPONENT_TOOLS` and all globals unchanged.)

- [ ] **Step 4: Run, verify it passes** — same command → PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/app/core/config.py tests/test_component_robustness.py
git commit -m "feat(config): component max_turns 8->12, near_cap 6->8"
```

**Tests/verification:** unit above.
**Rollback strategy:** revert commit; values restore to 8/6.
**Success criteria:** constants are 12 / 8; `COMPONENT_TOOLS` and globals unchanged.

---

## Task 2 — Anti-exploration + target-first `component_prompt`

**Files:** Modify `backend/app/orchestrator/component_validator.py` (`component_prompt`); Test: `tests/test_component_robustness.py` (update existing `test_component_prompt_has_preflight_and_path`).

**Exact behavior change:** Rewrite the preflight block: first action MUST be Write; forbid dir inspection / workspace listing / probing the target / inspecting `/root/.claude`/plugins; assume path correct; write build123d directly. Drop the `"Use the installed cad skill"` line. Keep build123d requirements + `def gen_step()`.

- [ ] **Step 1: Update the failing test**

Replace the body of `test_component_prompt_has_preflight_and_path` with:
```python
def test_component_prompt_has_preflight_and_path():
    p = cv.component_prompt(_SPEC, _COMP)
    assert "output/components/fuselage/generate.py" in p
    low = p.lower()
    assert "first" in low and "write" in low          # write-first directive
    assert "do not inspect" in low or "do not read or list" in low
    assert "plugins" in low                            # explicitly forbids plugin inspection
    assert "do not read" in low and "skill" in low     # forbids reading skill files
    assert "use the installed cad skill" not in low    # old skill-read line removed
    assert "def gen_step()" in p                       # build123d requirement preserved
```

- [ ] **Step 2: Run, verify it fails**

Run: `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_component_robustness.py::test_component_prompt_has_preflight_and_path -v`
Expected: FAIL (old prompt still has the skill line, lacks new phrases).

- [ ] **Step 3: Implement**

Replace the `return f"""..."""` body of `component_prompt` with:
```python
    bb = comp["target_bbox_mm"]
    return f"""PREFLIGHT — follow exactly:
- Your FIRST tool action MUST be `Write` to {comp['source']}. Do NOT Read or list anything before writing.
- Do NOT inspect directories, do not list the workspace, do not probe whether {comp['source']} exists,
  and do not read or inspect /root/.claude or any plugins. The target directory is created for you.
- Assume the target path is correct. Write the file immediately from your own build123d knowledge —
  do NOT read skill files or plugin files.
- Output EXACTLY ONE file at {comp['source']}. Create no other files. No shell. No Bash.
- After writing, STOP. The backend validates and, if needed, sends a targeted repair.

You are building ONE component of a {design_spec['object_class']}: the
`{comp['name']}` ({comp['role']}).

Write a STEP-first build123d source file at {comp['source']} defining exactly
`def gen_step():` that returns a SINGLE closed, positive-volume build123d Solid
(or a small Compound) for ONLY this component — not the whole assembly.

Requirements:
- Start with: from build123d import *
- Named parameters in millimeters near the top.
- Origin at the component's own center, XY base plane, +Z up.
- Approximate target envelope: {bb['x']} x {bb['y']} x {bb['z']} mm (a guide, not exact).
- Closed, positive-volume solid; manufacturable; fillets/chamfers where natural.
- No file/network I/O; no os/subprocess/socket/shutil/pathlib/requests,
  no open()/eval()/exec()/__import__.

The backend will STEP-export and inspect this component to validate it. Make it
a clean, recognizable {comp['name']}.
"""
```

- [ ] **Step 4: Run, verify it passes** — same command → PASS.

- [ ] **Step 5: Regression + commit**

Run: `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_component_robustness.py -q` → all pass.
```bash
git add backend/app/orchestrator/component_validator.py tests/test_component_robustness.py
git commit -m "feat(prompt): anti-exploration + write-first component prompt; drop skill-read line"
```

**Tests/verification:** unit above + full component_robustness file.
**Rollback strategy:** revert commit; prompt restores to the Task-3 preflight version.
**Success criteria:** prompt mandates Write-first, forbids exploration/plugin/skill reads, drops the skill line, keeps `def gen_step()` + path.

---

## Task 3 — Verification

> Real Claude + CAD. Each sub-phase its own gate. Clean up scratch projects after.

### 3.1 Unit tests (all)
- [ ] Run: `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/ -q` (all suites). **Success:** all pass.

### 3.2 Single-component rerun (live)
- [ ] Run a simple part ("mounting plate") through `claude_generation.run()`. From the raw log confirm the FIRST tool action is `Write` (not Read/dir-list); component valid; `component_metrics.json` turns < 12. **Success:** Write-first, turns < 12, valid.

### 3.3 Repair-path rerun (live)
- [ ] Seed the `translate` bug; run one component repair with the new prompt + 12-turn cap. **Success:** Edit-targeted, Bash off, recovers within budget.

### 3.4 4-object benchmark rerun
- [ ] Rerun calibration block, mounting plate, drone, gear housing. Capture per-object status/time/repairs and `component_metrics.json` totals. **Success target:** 4/4 COMPLETED; zero `FAILED_TURNS`.

### 3.5 Comparison report
- [ ] Append to `benchmark_summary.md`: turns/component and duration across the three configs (Bash-on/15, Bash-off/8, Bash-off/12); pass rates; confirm 4/4 recovered AND simple-object efficiency retained. **Success:** report committed with measured numbers.

---

## Self-review
**Spec coverage:** anti-exploration + write-first prompt (Task 2) ✓; drop skill-read line (Task 2) ✓; max_turns 12 / near_cap 8 (Task 1) ✓; preserved items untouched (Global Constraints) ✓; verification incl. single-component, repair-path, 4-object rerun, turns + duration comparison (Task 3) ✓.
**Placeholder scan:** none — full code in each step.
**Type consistency:** no signature changes; only constant values and prompt string body change.
