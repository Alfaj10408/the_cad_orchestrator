# Spec — Component-Level Turn Robustness

**Date:** 2026-06-21
**Status:** Approved design → implementation
**Scope:** Make each per-component Claude generation call turn-efficient and robust. Component generation is the only remaining Claude-bound stage after the deterministic-assembly refactor; this hardens it.

---

## 1. Problem

After the deterministic-assembly refactor, the only Claude calls are per-component generation. The 2026-06-19 benchmark showed the dominant failure was `error_max_turns`: Claude used the **Bash** tool to self-test / explore and burned through `max_turns=15` (`num_turns:16` → `is_error` → fail). Even when components eventually succeed, Bash self-test round-trips waste wall-clock time. Component prompts already say "Do NOT execute the code," but Claude still runs Bash because the tool is available.

## 2. Goals

- Each component call is turn-efficient: target ~3–5 turns (read skill → write file → stop).
- `error_max_turns` at component level is near-eliminated.
- Repair patches only the failing section (Edit), not full rewrites.
- Per-component diagnostics + metrics for visibility.

## 3. Non-goals (explicitly unchanged)

Frontend; deterministic assembly stage (graph/placement/composer/validation); production API; Qwen model choice; component decomposition; CAD modeling quality. No change to any non-component Claude call's defaults.

## 4. Decisions (approved)

- **Drop Bash for component calls**: component generation runs `tools = Read,Write,Edit` only. Backend owns all STEP export / inspection / validation, so Claude needs Read (cad skill files), Write (the source), Edit (repair) — never Bash. Hard-enforced.
- **Component `max_turns = 8`** (down from 15). Headroom for read-skill + write + one targeted Edit; fails fast into the repair loop otherwise.
- **Metrics in a separate `reports/component_metrics.json`** (not folded into `component_validation.json`, which stays the pass/fail gate artifact).

## 5. Architecture / changes

### 5.1 Adapter — `app/services/claude_code_adapter.py`
- `run_claude(...)` gains `tools: Optional[str] = None`. argv uses `tools or config.CLAUDE_CODE_TOOLS` for `--tools`. Default behavior unchanged when `tools` omitted.
- `run_claude` return dict gains `"num_turns"` (already captured internally as `result_num_turns`; surface it; `None` when unavailable).

### 5.2 Config — `app/core/config.py`
- `CLAUDE_CODE_COMPONENT_TOOLS = os.environ.get("CLAUDE_CODE_COMPONENT_TOOLS", "Read,Write,Edit")`
- `CLAUDE_CODE_COMPONENT_MAX_TURNS = int(os.environ.get("CLAUDE_CODE_COMPONENT_MAX_TURNS", "8"))`
- `CLAUDE_CODE_COMPONENT_NEAR_CAP = int(os.environ.get("CLAUDE_CODE_COMPONENT_NEAR_CAP", "6"))` (soft warn threshold)
- Existing global `CLAUDE_CODE_TOOLS` / `CLAUDE_CODE_MAX_TURNS` unchanged (used by any non-component call).

### 5.3 Prompts — `app/orchestrator/component_validator.py`
- `component_prompt`: prepend a hard preflight block:
  - "Output EXACTLY ONE file at `<source>`. Write code first. Do NOT execute, test, run, or explore. No shell. Create no other files. Stop after writing the file — the backend validates it."
  - Keep the existing build123d requirements + single `gen_step()` + single-solid emphasis.
- `repair_prompt`: instruct Claude to use the **Edit** tool to change ONLY the failing line/section identified from the error (the error tail already carries the real exception via `[-800:]`); do not rewrite the whole file. The prior source remains on disk so Edit applies.

### 5.4 Orchestration — `app/services/claude_generation.py`
- `_claude_call(prompt, *, tools=None, max_turns=None)` passes both through to `run_claude`.
- Component loop calls `_claude_call(cprompt, tools=config.CLAUDE_CODE_COMPONENT_TOOLS, max_turns=config.CLAUDE_CODE_COMPONENT_MAX_TURNS)`.
- Near-cap diagnostic: when a component call returns `num_turns >= CLAUDE_CODE_COMPONENT_NEAR_CAP`, publish a `cad.execution.log` warning event (`"component {name} near turn cap: {num_turns}/{max}"`).
- Metrics: accumulate one record per component across its attempts, write `reports/component_metrics.json` after the component loop (alongside the existing `write_report`).

### 5.5 Metrics artifact — `reports/component_metrics.json`
```json
{
  "project_id": "…",
  "components": [
    {
      "name": "fuselage",
      "attempts": 1,
      "repairs": 0,
      "turns_total": 4,
      "turns_per_attempt": [4],
      "failure_class": null,
      "source_bytes": 1820,
      "valid": true,
      "reason": null
    }
  ],
  "totals": {"components": 8, "passed": 8,
             "turns_total": 33, "repairs_total": 1,
             "avg_turns_per_component": 4.1}
}
```
- `failure_class` is the last non-null class seen for that component (None on success).
- `source_bytes` = byte length of the final written source (0 if none written).
- Collection happens in the component loop; emission is a small writer (mirrors `component_validator.write_report`). Writer location: a new `component_validator.write_metrics(project_id, records)` (keeps reporting helpers together).

## 6. Implementation phases

- **P1** Adapter: `tools` param + `num_turns` in return. (unit: argv contains passed tools; return has num_turns)
- **P2** Config: component tool/turn/near-cap constants. (unit: defaults present + env override)
- **P3** `component_prompt` preflight + single-file constraints. (unit: prompt contains preflight phrases + exact source path)
- **P4** `repair_prompt` Edit-targeted. (unit: contains Edit instruction + the passed error text)
- **P5** `component_validator.write_metrics` + record schema. (unit: schema keys, totals aggregation)
- **P6** Wire `_claude_call`/component loop: component tools+max_turns, near-cap event, metrics collection + write. (unit: spy that `_claude_call` is invoked with component tools+max_turns and quota/turns/cad mapping intact; metrics file written)
- **P7** Verification (below).

## 7. Tests

**Unit**
- Adapter: `run_claude` argv includes the `tools` value when passed; falls back to config default when omitted; return dict has `num_turns`.
- Config: component constants exist with documented defaults; env override respected.
- `component_prompt`: contains the preflight block ("ONE file", "Do NOT execute", "No shell", "Stop after") and the exact `comp["source"]` path.
- `repair_prompt`: contains an Edit-targeted instruction and the error string passed in.
- `write_metrics`: given component records, writes `component_metrics.json` with correct per-component fields and `totals` aggregation.
- Wiring: monkeypatch `run_claude`; assert the component loop calls it with `tools=CLAUDE_CODE_COMPONENT_TOOLS` and `max_turns=CLAUDE_CODE_COMPONENT_MAX_TURNS`; quota→FAILED_QUOTA / turns→FAILED_TURNS / cad→repair mapping unchanged; `component_metrics.json` produced.

**Integration / live**
- One component generated with Bash disabled completes (writes source, validates) in `num_turns` well under 8; `component_metrics.json` records it.
- 4-object mini-benchmark rerun (calibration block, mounting plate, drone, gear housing) capturing per-component turns/repairs + wall-clock; compare against the 2026-06-21 post-refactor run.

## 8. Expected impact

- **Turns/component:** from up to 16 (max_turns) to ~3–5. `error_max_turns` at component level near-eliminated (no Bash to loop on; `max_turns=8` fails fast into targeted repair).
- **Benchmark time:** hierarchical wall-clock is dominated by component-gen Claude latency; removing Bash self-test round-trips should cut per-component time materially (drone/gear ~32–37 min expected to drop; exact TBD by rerun).
- **Reliability:** fewer turn-exhaustion failures; targeted Edit repair raises repair success rate; metrics give per-component visibility.

## 9. Key uncertainty / risk

Dropping Bash removes Claude's ability to self-catch errors before submitting → possibly **more first-attempt validation failures** (caught by the backend) → **more repair rounds**. Net turn count should still drop (repair is also Bash-less and Edit-targeted; repair budget stays `CLAUDE_CODE_MAX_REPAIRS=2`). The benchmark rerun measures the tradeoff; `component_metrics.json` (`repairs_total`, `avg_turns_per_component`) exposes a regression if repair rounds spike. Mitigation lever if needed: raise `CLAUDE_CODE_COMPONENT_MAX_TURNS` via env without code change.

## 10. Out of scope (restated)

Frontend, deterministic assembly, production API, Qwen model choice, decomposition, CAD modeling quality. No change to non-component Claude-call defaults.
