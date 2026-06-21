# Component-Level Turn Robustness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each per-component Claude generation call turn-efficient and robust by dropping the Bash tool, lowering the turn cap, tightening the prompt, targeting repair to the failing section, and recording per-component metrics.

**Architecture:** Component generation is the only remaining Claude-bound stage. We make `run_claude` accept a per-call `tools` set and surface `num_turns`; run component calls with `Read,Write,Edit` (no Bash) and `max_turns=8`; reinforce with a preflight prompt; switch repair to Edit-targeted; collect per-component metrics into `reports/component_metrics.json`. No change to the deterministic assembly stage or any non-component Claude-call defaults.

**Tech Stack:** Python 3.11 (`/root/anaconda3/envs/cadskills/bin/python`), pytest, FastAPI backend.

## Global Constraints

- No frontend changes. No deterministic-assembly changes. No production-API changes. No Qwen model-choice changes. No component-decomposition or CAD-modeling-quality changes.
- Component calls run `tools = Read,Write,Edit` (NO Bash) and `max_turns = 8`. Backend owns all STEP export / inspection / validation.
- Do NOT change defaults for any non-component Claude call (global `CLAUDE_CODE_TOOLS` / `CLAUDE_CODE_MAX_TURNS` stay `Read,Write,Edit,Bash` / `15`).
- Metrics live in a SEPARATE `reports/component_metrics.json`; `component_validation.json` stays the pass/fail gate artifact.
- Repair budget stays `CLAUDE_CODE_MAX_REPAIRS = 2`.
- Tests live at product-root `tests/` (each self-inserts `backend/` on `sys.path`); run from product root `/root/all_project_models/alfaj/text-to-cad-product`. `cad_runner` sets `LD_PRELOAD` internally; pytest needs no special env.
- Python: `/root/anaconda3/envs/cadskills/bin/python`. Git: product-root repo; commit each task.

---

## File structure

| File | Responsibility | New/Mod |
|---|---|---|
| `backend/app/services/claude_code_adapter.py` | `build_claude_argv` helper + per-call `tools` + `num_turns` in return | Mod |
| `backend/app/core/config.py` | Component tool/turn/near-cap constants | Mod |
| `backend/app/orchestrator/component_validator.py` | Preflight `component_prompt`; Edit-targeted `repair_prompt`; `write_metrics` | Mod |
| `backend/app/services/claude_generation.py` | `_claude_call` per-call tools/max_turns; component-loop wiring + metrics + near-cap event | Mod |
| `tests/test_component_robustness.py` | All new unit tests | New |

---

## Task 1 — Adapter: per-call `tools` + `num_turns` in return

**Files:**
- Modify: `backend/app/services/claude_code_adapter.py`
- Test: `tests/test_component_robustness.py`

**Interfaces — Produces:**
- `build_claude_argv(*, prompt: str, model: str, max_turns: int, tools: str) -> list[str]` — pure argv builder.
- `run_claude(..., tools: Optional[str] = None)` — uses `tools or config.CLAUDE_CODE_TOOLS`; return dict gains `"num_turns"`.

**Exact behavior change:** Extract argv construction into a pure helper so tool/turn plumbing is testable; `run_claude` gains a `tools` kwarg and returns `num_turns`.

- [ ] **Step 1: Write the failing test**
```python
# tests/test_component_robustness.py
import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services import claude_code_adapter as adapter


def test_build_claude_argv_uses_passed_tools_and_turns():
    argv = adapter.build_claude_argv(prompt="hi", model="sonnet", max_turns=8,
                                     tools="Read,Write,Edit")
    assert "--tools" in argv
    assert argv[argv.index("--tools") + 1] == "Read,Write,Edit"
    assert "--max-turns" in argv
    assert argv[argv.index("--max-turns") + 1] == "8"
    assert "--model" in argv and argv[argv.index("--model") + 1] == "sonnet"
    assert argv[-1] == "hi"  # prompt is the final argv value
    assert "Bash" not in argv[argv.index("--tools") + 1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_component_robustness.py::test_build_claude_argv_uses_passed_tools_and_turns -v`
Expected: FAIL (`build_claude_argv` not defined).

- [ ] **Step 3: Implement**

In `claude_code_adapter.py`, add the pure helper near the top of the module (after imports / `classify_failure`):
```python
def build_claude_argv(*, prompt: str, model: str, max_turns: int, tools: str) -> list[str]:
    """Pure argv builder for the headless Claude CLI (testable)."""
    return [
        config.CLAUDE_CODE_BINARY, "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--permission-mode", config.CLAUDE_CODE_PERMISSION_MODE,
        "--tools", tools,
        "--model", model,
        "--max-turns", str(max_turns),
        prompt,
    ]
```
Then in `run_claude`, (a) add the param to the signature: `tools: Optional[str] = None,` (next to `max_turns`), and (b) replace the inline `argv = [...]` block with:
```python
    argv = build_claude_argv(
        prompt=prompt,
        model=model or config.CLAUDE_CODE_MODEL,
        max_turns=max_turns or config.CLAUDE_CODE_MAX_TURNS,
        tools=tools or config.CLAUDE_CODE_TOOLS,
    )
```
Finally add `"num_turns": result_num_turns,` to the SUCCESS return dict (the one with `"failure_class": failure_class,`), and add `"num_turns": None,` to the four early-return dicts (cancelled-before-start, binary-not-found, timeout, cancelled) so the key is always present.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_component_robustness.py::test_build_claude_argv_uses_passed_tools_and_turns -v`
Expected: PASS.

- [ ] **Step 5: Regression + commit**

Run: `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_claude_code.py tests/test_failure_classifier.py -q` → expect all pass.
```bash
git add backend/app/services/claude_code_adapter.py tests/test_component_robustness.py
git commit -m "feat(adapter): per-call tools + num_turns in run_claude return"
```

**Tests/verification:** unit above + adapter regression.
**Rollback strategy:** revert the commit; `build_claude_argv` is additive and `tools`/`num_turns` default to prior behavior, so reverting is clean.
**Success criteria:** `build_claude_argv` honors passed tools/turns; `run_claude` accepts `tools`, returns `num_turns`; existing adapter tests still pass.

---

## Task 2 — Config: component tool/turn/near-cap constants

**Files:**
- Modify: `backend/app/core/config.py`
- Test: `tests/test_component_robustness.py`

**Interfaces — Produces:** `config.CLAUDE_CODE_COMPONENT_TOOLS` (str), `config.CLAUDE_CODE_COMPONENT_MAX_TURNS` (int), `config.CLAUDE_CODE_COMPONENT_NEAR_CAP` (int).

**Exact behavior change:** Add three env-overridable constants; global defaults untouched.

- [ ] **Step 1: Write the failing test**
```python
# add to tests/test_component_robustness.py
from app.core import config as cfg

def test_component_config_defaults():
    assert cfg.CLAUDE_CODE_COMPONENT_TOOLS == "Read,Write,Edit"
    assert "Bash" not in cfg.CLAUDE_CODE_COMPONENT_TOOLS
    assert cfg.CLAUDE_CODE_COMPONENT_MAX_TURNS == 8
    assert cfg.CLAUDE_CODE_COMPONENT_NEAR_CAP == 6
    # globals unchanged
    assert cfg.CLAUDE_CODE_TOOLS == "Read,Write,Edit,Bash"
    assert cfg.CLAUDE_CODE_MAX_TURNS == 15
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_component_robustness.py::test_component_config_defaults -v`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Implement**

In `config.py`, after the existing `CLAUDE_CODE_TOOLS` / `CLAUDE_CODE_MAX_TURNS` lines, add:
```python
# Component-generation overrides (turn robustness). No Bash: backend owns
# STEP export/inspection, so component calls only Read/Write/Edit.
CLAUDE_CODE_COMPONENT_TOOLS = os.environ.get("CLAUDE_CODE_COMPONENT_TOOLS", "Read,Write,Edit")
CLAUDE_CODE_COMPONENT_MAX_TURNS = int(os.environ.get("CLAUDE_CODE_COMPONENT_MAX_TURNS", "8"))
CLAUDE_CODE_COMPONENT_NEAR_CAP = int(os.environ.get("CLAUDE_CODE_COMPONENT_NEAR_CAP", "6"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: same as Step 2 → PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/app/core/config.py tests/test_component_robustness.py
git commit -m "feat(config): component tool/turn/near-cap constants"
```

**Tests/verification:** unit above.
**Rollback strategy:** revert commit; constants are additive and unused until Task 6.
**Success criteria:** constants present with documented defaults; globals unchanged.

---

## Task 3 — Preflight `component_prompt`

**Files:**
- Modify: `backend/app/orchestrator/component_validator.py` (`component_prompt`)
- Test: `tests/test_component_robustness.py`

**Interfaces — Produces:** `component_prompt(design_spec, comp) -> str` (same signature) with a leading preflight constraint block.

**Exact behavior change:** Prepend a hard preflight block enforcing one-file / code-first / no-execution; keep existing build123d requirements.

- [ ] **Step 1: Write the failing test**
```python
# add to tests/test_component_robustness.py
from app.orchestrator import component_validator as cv

_SPEC = {"object_class": "quadcopter drone assembly"}
_COMP = {"name": "fuselage", "role": "central body",
         "source": "output/components/fuselage/generate.py",
         "target_bbox_mm": {"x": 112.5, "y": 112.5, "z": 60.0}}

def test_component_prompt_has_preflight_and_path():
    p = cv.component_prompt(_SPEC, _COMP)
    assert "output/components/fuselage/generate.py" in p
    low = p.lower()
    assert "exactly one file" in low
    assert "do not execute" in low
    assert "no shell" in low
    assert "stop after" in low
    assert "def gen_step()" in p  # existing requirement preserved
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_component_robustness.py::test_component_prompt_has_preflight_and_path -v`
Expected: FAIL (preflight phrases absent).

- [ ] **Step 3: Implement**

Replace the `return f"""..."""` body of `component_prompt` so it begins with the preflight block, keeping the existing content after it:
```python
    bb = comp["target_bbox_mm"]
    return f"""PREFLIGHT — follow exactly:
- Output EXACTLY ONE file at {comp['source']}. Create no other files.
- Write the build123d code FIRST. Do NOT execute, run, test, or explore.
- No shell. No Bash. Stop after writing the file — the backend validates it.

Use the installed cad skill (read its conventions; do not run anything).

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

- [ ] **Step 4: Run test to verify it passes**

Run: same as Step 2 → PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/app/orchestrator/component_validator.py tests/test_component_robustness.py
git commit -m "feat(prompt): preflight one-file/code-first component prompt"
```

**Tests/verification:** unit above.
**Rollback strategy:** revert commit; prompt text change only, no callers affected.
**Success criteria:** prompt contains the preflight phrases + the exact source path + `def gen_step()`.

---

## Task 4 — Edit-targeted `repair_prompt`

**Files:**
- Modify: `backend/app/orchestrator/component_validator.py` (`repair_prompt`)
- Test: `tests/test_component_robustness.py`

**Interfaces — Produces:** `repair_prompt(comp, reason) -> str` (same signature), now instructing an Edit-targeted patch.

**Exact behavior change:** Tell Claude to Edit ONLY the failing line/section identified from the error; do not rewrite the file; no execution.

- [ ] **Step 1: Write the failing test**
```python
# add to tests/test_component_robustness.py
def test_repair_prompt_is_edit_targeted_and_carries_error():
    r = cv.repair_prompt(_COMP, "NameError: name 'translate' is not defined")
    low = r.lower()
    assert "edit" in low
    assert "smallest" in low or "only the failing" in low
    assert "do not rewrite" in low or "do not execute" in low
    assert "NameError: name 'translate' is not defined" in r  # exact error included
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_component_robustness.py::test_repair_prompt_is_edit_targeted_and_carries_error -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Replace `repair_prompt`:
```python
def repair_prompt(comp: dict, reason: str) -> str:
    return (
        f"\n\n--- REWRITE REQUIRED (component {comp['name']}) ---\n{reason}\n"
        "Use the Edit tool to change ONLY the failing line/section identified by "
        "the error above. Do not rewrite the whole file. Do not execute or test. "
        "Make the smallest fix that yields a single closed positive-volume solid; "
        "the backend re-validates.\n"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: same as Step 2 → PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/app/orchestrator/component_validator.py tests/test_component_robustness.py
git commit -m "feat(prompt): Edit-targeted component repair"
```

**Tests/verification:** unit above.
**Rollback strategy:** revert commit; text change only.
**Success criteria:** repair prompt is Edit-targeted, forbids rewrite/execute, includes the error verbatim.

---

## Task 5 — `write_metrics` + record schema

**Files:**
- Modify: `backend/app/orchestrator/component_validator.py` (add `write_metrics`)
- Test: `tests/test_component_robustness.py`

**Interfaces — Produces:** `write_metrics(project_id: str, records: list[dict]) -> dict` — writes `reports/component_metrics.json`, returns the full report (with `totals`).

**Exact behavior change:** New reporting helper (mirrors `write_report`). Each record: `name, attempts, repairs, turns_total, turns_per_attempt, failure_class, source_bytes, valid, reason`. Computes `totals`.

- [ ] **Step 1: Write the failing test**
```python
# add to tests/test_component_robustness.py
import json
from app.core import paths

def test_write_metrics_schema_and_totals():
    pid = "metrics_t"
    records = [
        {"name": "a", "attempts": 1, "repairs": 0, "turns_total": 4,
         "turns_per_attempt": [4], "failure_class": None, "source_bytes": 1800,
         "valid": True, "reason": None},
        {"name": "b", "attempts": 2, "repairs": 1, "turns_total": 9,
         "turns_per_attempt": [5, 4], "failure_class": None, "source_bytes": 1500,
         "valid": True, "reason": None},
    ]
    rep = cv.write_metrics(pid, records)
    assert rep["totals"]["components"] == 2
    assert rep["totals"]["passed"] == 2
    assert rep["totals"]["turns_total"] == 13
    assert rep["totals"]["repairs_total"] == 1
    assert round(rep["totals"]["avg_turns_per_component"], 1) == 6.5
    on_disk = json.loads((paths.project_dir(pid) / "reports" / "component_metrics.json").read_text())
    assert on_disk["components"][0]["name"] == "a"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_component_robustness.py::test_write_metrics_schema_and_totals -v`
Expected: FAIL (`write_metrics` not defined).

- [ ] **Step 3: Implement**

Add to `component_validator.py`:
```python
def write_metrics(project_id: str, records: list[dict]) -> dict:
    passed = sum(1 for r in records if r.get("valid"))
    turns_total = sum(r.get("turns_total") or 0 for r in records)
    repairs_total = sum(r.get("repairs") or 0 for r in records)
    n = len(records)
    report = {
        "project_id": project_id,
        "components": records,
        "totals": {
            "components": n,
            "passed": passed,
            "turns_total": turns_total,
            "repairs_total": repairs_total,
            "avg_turns_per_component": (turns_total / n) if n else 0,
        },
    }
    reports = paths.project_dir(project_id) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "component_metrics.json").write_text(json.dumps(report, indent=2))
    return report
```

- [ ] **Step 4: Run test to verify it passes**

Run: same as Step 2 → PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/app/orchestrator/component_validator.py tests/test_component_robustness.py
git commit -m "feat(metrics): component_metrics.json writer"
```

**Tests/verification:** unit above.
**Rollback strategy:** revert commit; additive helper, no callers until Task 6.
**Success criteria:** `write_metrics` writes the file with correct per-component fields and `totals`.

---

## Task 6 — Wire component loop: component tools/max_turns + near-cap event + metrics

**Files:**
- Modify: `backend/app/services/claude_generation.py` (`_claude_call`, component loop)
- Test: `tests/test_component_robustness.py`

**Interfaces — Consumes:** Tasks 1,2,5. `_claude_call(prompt, *, tools=None, max_turns=None)`.

**Exact behavior changes:**
1. `_claude_call` accepts `tools`/`max_turns` and passes them to `run_claude`.
2. Component loop calls `_claude_call(cprompt, tools=config.CLAUDE_CODE_COMPONENT_TOOLS, max_turns=config.CLAUDE_CODE_COMPONENT_MAX_TURNS)`.
3. Per-component metric record accumulated across attempts (`turns_per_attempt` from `res.get("num_turns")`, `repairs`, `failure_class`, `source_bytes`, `valid`, `reason`); after the loop call `component_validator.write_metrics(project_id, metric_records)`.
4. Near-cap event: when a call's `num_turns >= config.CLAUDE_CODE_COMPONENT_NEAR_CAP`, publish a `cad.execution.log` warning.

- [ ] **Step 1: Write the failing test** (monkeypatch `run_claude`; assert component tools/max_turns used, metrics written)
```python
# add to tests/test_component_robustness.py
import asyncio
from app.services import claude_generation as cg, claude_code_adapter, job_service

VALID_COMP = ("from build123d import *\n\n"
              "def gen_step():\n    return fillet(Box(40,40,28).edges(), 2)\n")

def _seed(pid, prompt, dims):
    paths.ensure_project_skeleton(pid)
    (paths.project_dir(pid) / "brief.json").write_text(json.dumps({
        "project_id": pid, "prompt": prompt, "intent": "concept_cad",
        "parameters": {"dimensions": dims, "units": "mm", "material": "PLA"},
        "user_answers": {"dimensions": dims}, "ready_to_generate": True,
        "generation_mode": "qwen_claude_code"}))

def test_component_loop_uses_component_tools_and_writes_metrics(monkeypatch):
    pid = "wire_comp_tools"
    _seed(pid, "create a 3D gear housing", "100 x 100 x 60 mm")
    job = job_service.create_job_full(pid, "generation", "CREATED")
    seen = {"tools": set(), "max_turns": set()}
    async def fake_run_claude(project_id, job_id, prompt, ch, *, tools=None,
                              max_turns=None, model=None, timeout=None):
        seen["tools"].add(tools); seen["max_turns"].add(max_turns)
        # emulate Claude writing the component source named in the prompt
        import re
        m = re.search(r"output/components/(\S+?)/generate\.py", prompt)
        rel = m.group(0)
        dst = claude_code_adapter.safe_workspace_path(
            claude_code_adapter.ensure_workspace(project_id), rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(VALID_COMP)
        return {"ok": True, "failure_class": None, "num_turns": 4,
                "session_id": "s", "result_text": "done", "exit_code": 0, "error": None}
    monkeypatch.setattr(claude_code_adapter, "run_claude", fake_run_claude)
    asyncio.run(cg.run(pid, job.job_id))
    assert seen["tools"] == {"Read,Write,Edit"}
    assert seen["max_turns"] == {8}
    metrics = json.loads((paths.project_dir(pid) / "reports" / "component_metrics.json").read_text())
    assert metrics["totals"]["components"] >= 1
    assert all("turns_total" in c for c in metrics["components"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_component_robustness.py::test_component_loop_uses_component_tools_and_writes_metrics -v`
Expected: FAIL (`_claude_call` ignores tools; no metrics file).

- [ ] **Step 3: Implement**

(a) Update `_claude_call`:
```python
        async def _claude_call(prompt: str, *, tools=None, max_turns=None) -> tuple[str, dict]:
            """One Claude call. Returns (status, res); status: ok|fail|cancel.

            Also surfaces res['failure_class']: None | 'quota' | 'turns' | 'cad'.
            """
            res = await claude_code_adapter.run_claude(
                project_id, job_id, prompt, ch, tools=tools, max_turns=max_turns)
            if res.get("error") == "cancelled" or job_id in claude_code_adapter._cancelled:
                return "cancel", res
            return ("ok" if res["ok"] else "fail"), res
```

(b) In the component loop, initialize a metrics accumulator before the `for comp` loop:
```python
        metric_records: list[dict] = []
```
Inside the `for comp` loop, before `while True:`, init per-component trackers:
```python
            turns_per_attempt: list[int] = []
            last_fc = None
```
Change the call line to pass component tools/turns:
```python
                status, res = await _claude_call(
                    cprompt,
                    tools=config.CLAUDE_CODE_COMPONENT_TOOLS,
                    max_turns=config.CLAUDE_CODE_COMPONENT_MAX_TURNS)
```
Immediately after that call, record turns + near-cap event:
```python
                nt = res.get("num_turns")
                if nt is not None:
                    turns_per_attempt.append(nt)
                    if nt >= config.CLAUDE_CODE_COMPONENT_NEAR_CAP:
                        await ch.publish(SOURCE_WORKER, "cad.execution.log", stage="validation",
                                         message=f"Component {comp['name']} near turn cap: "
                                                 f"{nt}/{config.CLAUDE_CODE_COMPONENT_MAX_TURNS}")
                if res.get("failure_class"):
                    last_fc = res["failure_class"]
```
At BOTH places where the inner `while` ends for this component (the `comp["status"] = "valid"; results.append(v); break` success path AND the `c_repair > MAX_REPAIRS` exhaustion path), append a metric record. To DRY this, add a small local helper just before the `for comp` loop:
```python
        def _src_bytes(c) -> int:
            cp = claude_code_adapter.safe_workspace_path(
                claude_code_adapter.workspace_dir(project_id), c["source"])
            try:
                return len(cp.read_text().encode("utf-8")) if cp and cp.is_file() else 0
            except Exception:  # noqa: BLE001
                return 0
```
Success path — change:
```python
                            comp["status"] = "valid"; results.append(v)
                            metric_records.append({
                                "name": comp["name"], "attempts": c_repair + 1,
                                "repairs": c_repair, "turns_total": sum(turns_per_attempt),
                                "turns_per_attempt": turns_per_attempt, "failure_class": last_fc,
                                "source_bytes": _src_bytes(comp), "valid": True, "reason": None})
                            break
```
Exhaustion path — change:
```python
                if c_repair > config.CLAUDE_CODE_MAX_REPAIRS:
                    comp["status"] = "invalid"
                    results.append({"name": comp["name"], "step": comp["step"],
                                    "valid": False, "reason": c_reason, "facts": None})
                    metric_records.append({
                        "name": comp["name"], "attempts": c_repair,
                        "repairs": c_repair, "turns_total": sum(turns_per_attempt),
                        "turns_per_attempt": turns_per_attempt, "failure_class": last_fc,
                        "source_bytes": _src_bytes(comp), "valid": False, "reason": c_reason})
                    break
```
After the `for comp` loop, after `write_report(...)`, add:
```python
        component_validator.write_metrics(project_id, metric_records)
```
(The quota/turns immediate-abort paths still `return` before metrics — acceptable; a full metrics file is only meaningful for runs that complete the component loop. Leave those returns as-is.)

- [ ] **Step 4: Run test to verify it passes**

Run: same as Step 2 → PASS.

- [ ] **Step 5: Regression + commit**

Run: `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_claude_generation_wiring.py tests/test_simple_part_path.py tests/test_component_robustness.py -q` → expect all pass (quota/turns/cad mapping + deterministic assembly unaffected).
```bash
git add backend/app/services/claude_generation.py tests/test_component_robustness.py
git commit -m "feat: component calls use Read/Write/Edit + max_turns=8; per-component metrics + near-cap event"
```

**Tests/verification:** unit above + wiring/simple-part regression.
**Rollback strategy:** revert this commit only; Tasks 1-5 remain (inert without this wiring). Component calls revert to global tools/turns.
**Success criteria:** component loop invokes `run_claude` with `Read,Write,Edit` + `max_turns=8`; `component_metrics.json` written; quota/turns/cad mapping and deterministic assembly unchanged; near-cap event emitted when turns ≥ 6.

---

## Task 7 — Verification

> Real CAD + (limited) real Claude. Each sub-phase is its own gate. Run when Claude quota is available.

### 7.1 Unit tests (all)
- [ ] Run: `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_component_robustness.py tests/test_assembly_graph.py tests/test_placement_rules.py tests/test_assembly_composer.py tests/test_assembly_validate.py tests/test_failure_classifier.py tests/test_claude_generation_wiring.py tests/test_simple_part_path.py tests/test_orchestrator.py tests/test_claude_code.py -v`
- **Success:** all pass. **Rollback:** none (read-only). **Files:** none.

### 7.2 Single-component generation test (live, Bash disabled)
- [ ] Run one real component generation through `cg.run()` on a 1-component (simple-part) project (e.g. "mounting plate") with real Claude. Confirm: source written, component validates, `component_metrics.json` shows the component with `turns_total` recorded and well under 8; no `error_max_turns`.
- **Success:** component COMPLETED, turns < 8, metrics recorded. **Rollback:** none (scratch project; clean up). **Files:** scratch only.

### 7.3 Repair-path test (live or seeded)
- [ ] Seed a deliberately broken component source (e.g. the known `translate()` bug) and run the component repair loop (real Claude, Edit-targeted, Bash disabled). Confirm: repair patches the failing section via Edit, component then validates, `component_metrics.json` shows `repairs >= 1` and the recovery.
- **Success:** repair recovers within budget; metrics show repair count. **Rollback:** none (scratch). **Files:** scratch only.

### 7.4 Turn-count comparison
- [ ] From 7.2/7.3 + 7.5 metrics, tabulate turns-per-component (new, Bash off) vs the pre-change baseline (component calls previously could reach 16). Record avg turns/component before vs after.
- **Success:** measurable reduction in avg turns/component; zero `error_max_turns` at component level. **Rollback:** none. **Files:** none.

### 7.5 4-object benchmark rerun
- [ ] Rerun the 4-object mini-benchmark (calibration block, mounting plate, drone, gear housing) via the existing scratchpad harness (component calls now Bash-off, max_turns=8). Capture per-object: status, gen_time, component pass, repairs, and read `component_metrics.json` totals (avg turns/component, repairs_total).
- **Success:** ≥ prior 4/4 pass maintained; wall-clock per hierarchical object reduced; zero `FAILED_TURNS`. **Rollback:** none (scratch projects; clean up). **Files:** scratch only.

### 7.6 Final report (explicit measurements)
- [ ] Produce a short report (append to `benchmark_summary.md`) measuring, before vs after:
  - **turns per component** (avg, from `component_metrics.json`)
  - **repairs per component** (avg + total)
  - **benchmark duration** (per-object + total wall-clock for the 4 objects)
  - **component success rate** (components passed / total)
  - **effect of Bash removal** (turns delta, repair-round delta, any first-attempt-failure increase — the spec's key risk)
- **Success:** report committed with measured numbers. **Rollback:** none (doc). **Files:** `benchmark_summary.md`.

---

## Self-review

**Spec coverage:** drop Bash for component calls (Tasks 1,2,6) ✓; `max_turns=8` (Tasks 2,6) ✓; preflight one-file/code-first prompt (Task 3) ✓; Edit-targeted repair (Task 4) ✓; per-component metrics → `component_metrics.json` (Tasks 5,6) ✓; near-cap diagnostic (Task 6) ✓; `num_turns` surfaced (Task 1) ✓; separate metrics file, globals unchanged (Global Constraints, Tasks 2,6) ✓; verification: unit / single-component / repair-path / turn-count / 4-object rerun / measured final report (Task 7.1–7.6) ✓; no frontend/assembly/API/Qwen changes (Global Constraints) ✓.
**Placeholder scan:** none — every code step has full code.
**Type consistency:** `build_claude_argv(*, prompt, model, max_turns, tools)->list[str]`; `run_claude(..., tools=None)` returns `num_turns`; `_claude_call(prompt, *, tools=None, max_turns=None)`; `write_metrics(project_id, records)->dict`; metric record keys identical across Tasks 5 and 6 (`name, attempts, repairs, turns_total, turns_per_attempt, failure_class, source_bytes, valid, reason`). Consistent.
