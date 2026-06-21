# Spec — Error-Aware Repair Hints

**Date:** 2026-06-21
**Status:** Approved design → implementation
**Scope:** Improve component repair convergence by appending build123d-specific remediation hints to the repair prompt for known failure classes. One file.

## Problem (evidence)
10-object benchmark: drone `FAILED_CAD` at component validation — only `arm` failed, with a build123d `FilletError` ("creating a fillet with radius of 0.2, try a smaller value or use max_fillet()"). Across 3 attempts (2 repairs) Claude kept the same invalid `fillet(...,0.2)` and never converged. The generic repair instruction ("fix the smallest part") didn't teach the model *how* to fix a fillet failure. Not a turn/exploration issue — turns were low (2–3/attempt), no `FAILED_TURNS`.

## Fix
Add `component_validator._repair_hint(reason) -> str`: conservative lowercased-substring match on the error → a specific remediation hint. `repair_prompt` appends it after the existing Edit-targeted instruction. Generic fallback (empty hint) for unmatched errors.

| Error signature (substring, lowercased) | Hint |
|---|---|
| `fillet` AND (`radius` OR `max_fillet`) | reduce radius / use `max_fillet()` / remove the fillet |
| `chamfer` | reduce chamfer length / remove the chamfer |
| `not defined` OR `nameerror` OR `attributeerror` | use valid build123d API — `.moved(Location(...))`/`Pos(...)`, `Locations(...)`; no `translate()`/undefined names |
| `degenerate` OR `no solid` OR `empty` | ensure positive-volume solid; booleans not removing all material; dims > 0 |
| (no match) | "" |

The hint augments, never replaces, the verbatim error text + Edit instruction.

## Preserved (no change)
`CLAUDE_CODE_MAX_REPAIRS=2` (not raised), component tools `Read,Write,Edit`, `max_turns=12`, anti-exploration prompt, metrics, deterministic assembly, failure classes.

## Non-goals
Raising repair budget; component-generation prompt; Qwen prompts; assembly; frontend; API; CAD modeling quality beyond this repair-guidance text.

## Verification
1. Unit: `_repair_hint` returns correct hint per class; unknown → ""; `repair_prompt` still contains the reason verbatim + Edit instruction + the matched hint.
2. Live (decisive): seed an arm with a failing `fillet(edges, 0.2)` → run the repair loop → component recovers within budget (vs prior 3-attempt non-convergence).
3. Re-run the drone end-to-end → expect COMPLETED (arm recovers).
4. Optional: resume full 10-object benchmark.

**Success:** drone COMPLETES; fillet/geometry errors converge within the existing 2-repair budget.

## Risk
Mis-matched hint could misdirect. Mitigation: conservative substring matching; generic fallback preserved; hint is additive to the real error.
