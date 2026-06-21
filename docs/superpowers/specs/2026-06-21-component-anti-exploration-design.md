# Spec — Component Anti-Exploration + Turn-Budget Fix

**Date:** 2026-06-21
**Status:** Approved design → implementation
**Scope:** Recover the 4/4 benchmark pass rate lost when component `max_turns` dropped to 8, while keeping the Bash-removal efficiency gains. Two files: the component prompt + config.

## Problem (evidence)
With Bash off + `max_turns=8`, hierarchical objects regressed to `FAILED_TURNS`. Read-call breakdown showed the turns go to **exploratory reads** — workspace dir-listings, `/root/.claude/plugins` discovery, and probing the not-yet-existing `generate.py` — before the single productive `Write`. **Zero** CAD-skill content reads. 4–5 exploratory reads + thinking reach 6–9 turns; `max_turns=8` has no slack. Non-deterministic around the boundary (one repro: drone 9 turns FAIL, gear 6 turns PASS).

## Fix
1. **Anti-exploration + target-first prompt** (`component_validator.component_prompt`): first action MUST be `Write {source}`; forbid dir inspection / workspace listing / probing `generate.py` / inspecting `/root/.claude`/plugins; assume path correct; write build123d directly from own knowledge. **Drop** the `"Use the installed cad skill (read its conventions...)"` line — it triggers the plugin read.
2. **Turn budget** (`config.py`): `CLAUDE_CODE_COMPONENT_MAX_TURNS` 8 → **12**; `CLAUDE_CODE_COMPONENT_NEAR_CAP` 6 → **8**. (Slack for ≤2 incidental reads + write; below the wandering-prone 15.)

## Preserved (no change)
Bash disabled (`tools=Read,Write,Edit`); Edit-targeted repair; per-component metrics (`component_metrics.json`, incl. `duration_seconds`); deterministic assembly; failure classes; quota/turns abort; `write_report`.

## Non-goals
Qwen prompts, deterministic assembly, frontend, production API, CAD modeling quality, decomposition. No raising to 15.

## Verification
1. Single-component rerun (mounting plate): first action = Write, turns < 12, valid.
2. Repair-path rerun (seed `translate` bug): Edit-targeted, Bash off, recovers.
3. 4-object benchmark rerun (calibration, mounting, drone, gear).
4. Turns/component comparison: Bash-on/15 (baseline, uninstrumented) vs Bash-off/8 (regressed) vs Bash-off/12 (this fix).
5. Benchmark duration comparison.

**Success:** 4/4 pass restored (drone + gear COMPLETE) AND simple objects keep Bash-off efficiency (~30s, ≤4 turns).

## Risk
If exploration persists despite the prompt, 12 may still be marginal. Mitigations in place: env-overridable cap; metrics expose turns/component.
