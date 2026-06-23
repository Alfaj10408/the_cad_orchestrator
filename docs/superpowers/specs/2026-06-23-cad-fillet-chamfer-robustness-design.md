# Spec — CAD Fillet/Chamfer Robustness

**Date:** 2026-06-23
**Status:** Approved design → implementation plan
**Baseline:** `v0.3.0-multiworker-activation`.
**Scope:** CAD component-generation robustness ONLY. No API / multi-worker / production-hardening changes.

## Freeze acknowledgement
The effective fix edits `backend/app/orchestrator/component_validator.py`, frozen since `v0.1-benchmark-10of10`. The engine-freeze guard (`git diff v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator`) will become **non-empty for `component_validator.py`** — accepted for this CAD-robustness milestone. After implementation, **re-baseline the engine tag** (e.g. `v0.3.1`). Nothing else in `services/`/`orchestrator/` changes.

## Root cause
Component `internal_cavity_weight_relief` calls `chamfer()` on edges that a prior **global `fillet()`** already rounded/consumed (or that are curved). OCC cannot chamfer a non-existent/curved edge → the build raises → STEP export fails → component fails → airplane job fails. Mechanisms: (a) a global fillet replaces straight edges with curved faces, so a later chamfer referencing them errors; (b) chamfering a curved edge errors directly. Fillets/chamfers are cosmetic (no structural validity) yet are killing an otherwise-valid solid.

## Strategy (three layers)
- **(A) Prevention** — component prompt rules so Claude writes safe fillet/chamfer from the start.
- **(B) Fallback** — mandatory try/except so a failed cosmetic op degrades to the base solid.
- **(C) Targeted repair** — a precise repair hint for the edge-suitability failure when it still occurs.
(A)+(B) in `component_prompt()`; (C) in `_repair_hint()`; a minor planning note in `worker_prompt_system.txt`.

## Prevention — `component_prompt()` rules (component_validator.py)
Replace the line-38 "fillets/chamfers where natural" clause with explicit rules:
- **Cosmetic-only:** the component must be a valid closed positive-volume solid *without* any fillet/chamfer.
- **Never both on the same edge:** do not apply `fillet()` and `chamfer()` to the same edge or overlapping edge sets.
- **Order when sharing:** if both are needed on nearby edges, **chamfer before fillet** (chamfer needs the original straight edge; apply it first, then fillet the remaining straight edges).
- **Narrow edge selection:** never `part.edges()` globally for a cosmetic op; select a specific subset (e.g. `.edges().filter_by(Axis.Z)`, `.group_by(...)[...]`, one face's edges).
- **Straight edges only for chamfer:** chamfer only linear edges (`.filter_by(GeomType.LINE)`); never chamfer curved/filleted edges.
- **Conservative sizes:** small radius/length vs the local wall; prefer `max_fillet()` for a safe radius.

## Fallback — mandatory safe wrapper (component_prompt rule + example)
Rule text: *"Build the valid closed solid first. Apply each cosmetic fillet/chamfer inside its own try/except; on ANY exception keep the pre-cosmetic solid and continue. `gen_step()` MUST always return a valid positive-volume solid even if every cosmetic op fails."* Example embedded in the prompt:
```python
base = part            # valid closed solid before cosmetics
try:
    part = fillet(part.edges().filter_by(Axis.Z), radius=r)
except Exception:
    part = base        # cosmetic failed -> keep the valid base solid
```
This guarantees the fillet/chamfer failure class can no longer fail a component.

## Targeted repair — new `_repair_hint()` branch (component_validator.py)
Add a branch for edge-suitability failures, matching OCC/edge errors (`fillet`/`chamfer` co-occurring with `edge`/`no faces`/`brep`/`standard_`/`null`/`not found`). Insert it **before** the existing generic `chamfer` branch so it wins for these cases; keep the existing fillet-radius / chamfer-length / API-signature / degenerate branches.
```
HINT: this fillet/chamfer targets edges that no longer exist or are curved
(often because a prior global fillet already rounded them). Fixes, in order:
(1) wrap the cosmetic op in try/except and keep the base solid on failure;
(2) if fillet and chamfer share edges, do the chamfer BEFORE the fillet;
(3) select a narrow, specific edge set and chamfer only straight edges
(.filter_by(GeomType.LINE)); (4) if still failing, remove the cosmetic op —
the component is valid without it.
```
`repair_prompt()` already appends `_repair_hint(reason)` — no structural change.

## Planning-level note — `worker_prompt_system.txt` (not frozen)
Add one clause near the geometry-approach line: *"When specifying fillets/chamfers, treat them as cosmetic, never call for both on the same edge, and keep them small and local."* Reinforcement only; the real fix is the prompt/hint changes above.

## Code areas
- `backend/app/orchestrator/component_validator.py` (frozen) — `component_prompt()` fillet/chamfer prevention + mandatory fallback; new `_repair_hint()` edge-suitability branch.
- `backend/app/ai/prompts/worker_prompt_system.txt` (not frozen) — planning note.
- **No** change to `claude_generation.py`, `cad_runner`, assembly, API, multi-worker, hardening. No new dependency. No schema. Fallback is prompt-mandated (Claude writes the try/except) — NOT a Python wrapper around generated code.

## Non-goals
Python-side wrapping/parsing of generated CAD code; changes to the repair loop driver; assembly-level changes; the 10-object benchmark rerun; any API/multi-worker/hardening change.

## Testing / verification
Prompt/hint changes are LLM-driven — not deterministically unit-testable. Verification is live reruns:
1. **Failed component only:** rerun `internal_cavity_weight_relief` (isolated component harness) → STEP export + inspect pass; confirm a forced cosmetic-op failure now degrades to the base solid instead of failing the component.
2. **Airplane job end-to-end:** rerun the full airplane job → all components pass → assembly builds.
3. **Preview:** final **STEP + STL + GLB** produced; GLB preview opens.
Does NOT rerun the 10-object benchmark (heavy; CLAUDE.md: no long CAD runs unless asked) — optional follow-up.

## Release criteria
`internal_cavity_weight_relief` passes (incl. degrade-to-base on forced cosmetic failure); airplane job completes end-to-end; final STEP/STL/GLB preview opens; no API/multi-worker/hardening/schema changes; engine tag re-baselined after the (accepted) `component_validator.py` edit.
