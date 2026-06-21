# Hierarchical Deterministic Assembly Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the monolithic Claude assembly/single-shot calls with a deterministic, Claude-free assembly that composes validated component STEP files, persists an assembly graph, and classifies job failures as CAD/QUOTA/TURNS.

**Architecture:** After per-component generation+validation (unchanged), the backend builds an instance graph from the manifest, resolves per-node placement transforms in pure Python, emits a tiny machine-written `gen_step()` that `import_step()`s each validated component STEP and places it into a `Compound`, then runs the existing deterministic CAD export. No Claude call occurs in the assembly stage.

**Tech Stack:** Python 3.11 (`/root/anaconda3/envs/cadskills/bin/python`), build123d 0.10.0, pytest, FastAPI backend, the repo `cad` skill `step`/`inspect` tools (invoked via `cad_runner`).

## Global Constraints

- No frontend changes. No Qwen prompt changes. No component-generation prompt changes. No CAD modeling-quality changes.
- Assembly operates **exclusively** on validated component STEP files (`output/components/{name}/{name}.step`). NEVER re-execute component `gen_step()`.
- Emitted assembly source must reference component STEPs by **absolute path** (assembly export runs with `cwd=project_dir`; component STEPs live under `runs/{pid}/claude-workspace/`).
- No single Claude call may exceed ~5–7 turns; the assembly stage makes **zero** Claude calls (assert this in tests).
- Failure classes: `FAILED_CAD`, `FAILED_QUOTA`, `FAILED_TURNS`. Abort the job immediately on `FAILED_QUOTA` (no repair spin).
- Python invocation: `/root/anaconda3/envs/cadskills/bin/python`. Tests live at product-root `tests/` (each self-inserts `backend/` on `sys.path`); run from product root `/root/all_project_models/alfaj/text-to-cad-product`. `cad_runner` sets `LD_PRELOAD` internally; pytest does not need it.
- **Version control:** product root is not a git repo. "Commit" steps are optional checkpoints — substitute "rerun this task's tests green" or run `git init` once if desired.
- Preconditions (verify once before Task 1): `pytest` importable in the cadskills env (`python -m pytest --version`; if missing `pip install pytest`); build123d `import_step` present (already confirmed); existing suite green (`python -m pytest tests/test_orchestrator.py tests/test_claude_code.py -q`).

---

## File structure

| File | Responsibility | New/Mod |
|---|---|---|
| `backend/app/orchestrator/assembly_graph.py` | Build + persist the instance graph (`reports/assembly_graph.json`) | New |
| `backend/app/orchestrator/placement_rules.py` | Pure role→transform resolution; drone domain rules + generic grid fallback | New |
| `backend/app/orchestrator/assembly_composer.py` | Emit machine-written `gen_step()` (absolute-path `import_step` + `Compound`); write to workspace | New |
| `backend/app/orchestrator/assembly_builder.py` | Extend `validate_assembly` (structural checks); remove `assembly_prompt`/`repair_prompt` | Mod |
| `backend/app/services/claude_code_adapter.py` | Classify Claude result → `failure_class` in `run_claude` return | Mod |
| `backend/app/services/claude_generation.py` | Failure-class→status mapping + quota abort-fast; deterministic assembly stage; route simple parts; remove single-shot branch | Mod |
| `tests/test_assembly_graph.py` … `test_assembly_integration.py` | Tests | New |

---

## Phase 1 — Assembly graph (`assembly_graph.py`)

**Files:** Create `backend/app/orchestrator/assembly_graph.py`; Test `tests/test_assembly_graph.py`.

**Interfaces — Produces:**
- `build_graph(manifest: dict, design_spec: dict) -> dict` — graph with `nodes[]` (each: `id, component_type, instance_index, parent, role, step_file, target_bbox_mm, placement=None`), `node_count`, `envelope_mm`, `frame`, `placement_engine=None`.
- `write_graph(project_id: str, graph: dict) -> str` — writes `reports/assembly_graph.json`, returns path.

**Exact behavior change:** New pure builder. Expands `quantity` into instances (`arm` ×4 → `arm_0..arm_3`; qty 1 keeps bare name). No I/O except `write_graph`.

- [ ] **Step 1: Write failing test**
```python
# tests/test_assembly_graph.py
from app.orchestrator import assembly_graph

MANIFEST = {"project_id": "p", "components": [
    {"name": "fuselage", "quantity": 1, "role": "body",
     "target_bbox_mm": {"x": 112.5, "y": 112.5, "z": 60.0},
     "source": "output/components/fuselage/generate.py",
     "step": "output/components/fuselage/fuselage.step"},
    {"name": "arm", "quantity": 4, "role": "boom",
     "target_bbox_mm": {"x": 125.0, "y": 20.0, "z": 9.6},
     "source": "output/components/arm/generate.py",
     "step": "output/components/arm/arm.step"},
]}
SPEC = {"project_id": "p", "object_class": "drone", "object_kind": "quadcopter drone",
        "overall_envelope_mm": {"x": 250.0, "y": 250.0, "z": 120.0}}

def test_quantity_expansion_and_fields():
    g = assembly_graph.build_graph(MANIFEST, SPEC)
    ids = [n["id"] for n in g["nodes"]]
    assert ids == ["fuselage", "arm_0", "arm_1", "arm_2", "arm_3"]
    assert g["node_count"] == 5
    arm0 = g["nodes"][1]
    assert arm0["component_type"] == "arm" and arm0["instance_index"] == 0
    assert arm0["parent"] == "root"
    assert arm0["step_file"] == "output/components/arm/arm.step"
    assert arm0["placement"] is None
    assert g["envelope_mm"] == {"x": 250.0, "y": 250.0, "z": 120.0}
```

- [ ] **Step 2: Run, verify fail** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_assembly_graph.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement**
```python
# backend/app/orchestrator/assembly_graph.py
"""Deterministic assembly instance graph from the component manifest."""
from __future__ import annotations
import json
from app.core import paths


def build_graph(manifest: dict, design_spec: dict) -> dict:
    nodes = []
    for comp in manifest["components"]:
        qty = int(comp.get("quantity", 1) or 1)
        for i in range(qty):
            nid = comp["name"] if qty == 1 else f"{comp['name']}_{i}"
            nodes.append({
                "id": nid,
                "component_type": comp["name"],
                "instance_index": i,
                "parent": "root",
                "role": comp.get("role", ""),
                "step_file": comp["step"],
                "target_bbox_mm": comp.get("target_bbox_mm", {}),
                "placement": None,
            })
    env = design_spec["overall_envelope_mm"]
    return {
        "project_id": design_spec.get("project_id") or manifest.get("project_id", ""),
        "object_class": design_spec.get("object_class", ""),
        "object_kind": design_spec.get("object_kind", ""),
        "frame": {"id": "root", "origin_mm": [0, 0, 0],
                  "convention": "XY base, +Z up, mm"},
        "nodes": nodes,
        "node_count": len(nodes),
        "envelope_mm": env,
        "placement_engine": None,
    }


def write_graph(project_id: str, graph: dict) -> str:
    reports = paths.project_dir(project_id) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    out = reports / "assembly_graph.json"
    out.write_text(json.dumps(graph, indent=2))
    return str(out)
```

- [ ] **Step 4: Run, verify pass** — same pytest command → PASS.
- [ ] **Step 5: Checkpoint** (optional commit `feat: assembly graph builder`).

**Tests/verification:** pytest above.
**Rollback risk:** None — new isolated module, not yet wired.
**Success criteria:** Quantities expand to correct ids; fields present; `placement` starts `None`.

---

## Phase 2 — Placement rules (`placement_rules.py`)

**Files:** Create `backend/app/orchestrator/placement_rules.py`; Test `tests/test_placement_rules.py`.

**Interfaces — Consumes:** graph from Phase 1. **Produces:** `resolve(graph: dict, design_spec: dict) -> dict` — fills each node's `placement = {"translate_mm":[x,y,z], "rotate_deg":[rx,ry,rz], "rule":str}`; sets `graph["placement_engine"]`.

**Exact behavior change:** New pure resolver. Drone domain rules (radial arms 45/135/225/315°, pods at arm tips, props above pods, deck on top, gear/camera/battery on body). Unknown `object_kind` → deterministic centered grid in XY (non-overlapping by envelope/cols).

- [ ] **Step 1: Write failing test**
```python
# tests/test_placement_rules.py
import math
from app.orchestrator import assembly_graph, placement_rules

MAN = {"project_id": "p", "components": [
    {"name": "fuselage", "quantity": 1, "role": "body",
     "target_bbox_mm": {"x": 1, "y": 1, "z": 1}, "step": "output/components/fuselage/fuselage.step"},
    {"name": "arm", "quantity": 4, "role": "boom",
     "target_bbox_mm": {"x": 1, "y": 1, "z": 1}, "step": "output/components/arm/arm.step"},
]}
DRONE = {"project_id": "p", "object_kind": "quadcopter drone", "object_class": "drone",
         "overall_envelope_mm": {"x": 250.0, "y": 250.0, "z": 120.0}}
GENERIC = {"project_id": "p", "object_kind": "widget", "object_class": "widget",
           "overall_envelope_mm": {"x": 100.0, "y": 100.0, "z": 100.0}}

def test_drone_arms_radial_unique_angles():
    g = placement_rules.resolve(assembly_graph.build_graph(MAN, DRONE), DRONE)
    arms = [n for n in g["nodes"] if n["component_type"] == "arm"]
    angles = sorted(n["placement"]["rotate_deg"][2] for n in arms)
    assert angles == [45, 135, 225, 315]
    assert all(n["placement"]["rule"] == "radial_arm" for n in arms)
    assert g["placement_engine"] == "domain:quadcopter drone"

def test_generic_grid_is_filled_and_centered():
    g = placement_rules.resolve(assembly_graph.build_graph(MAN, GENERIC), GENERIC)
    assert g["placement_engine"] == "generic_layout"
    assert all(n["placement"] is not None for n in g["nodes"])
    xs = [n["placement"]["translate_mm"][0] for n in g["nodes"]]
    assert min(xs) <= 0 <= max(xs)  # centered around origin
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement**
```python
# backend/app/orchestrator/placement_rules.py
"""Deterministic component placement transforms. Zero Claude."""
from __future__ import annotations
import math


def _drone_xform(node: dict, env: dict) -> tuple[list, list, str]:
    name = node["component_type"]; i = node["instance_index"]
    x, y, z = env["x"], env["y"], env["z"]
    arm_r = 0.20 * x
    if name == "fuselage":
        return [0, 0, 0.35 * z], [0, 0, 0], "fuselage_center"
    if name == "arm":
        ang = 45 + i * 90; a = math.radians(ang)
        return [round(arm_r * math.cos(a), 2), round(arm_r * math.sin(a), 2), 0.35 * z], [0, 0, ang], "radial_arm"
    if name in ("motor_pod", "propeller"):
        ang = 45 + i * 90; a = math.radians(ang); r = arm_r + 0.25 * x
        zf = 0.40 * z if name == "motor_pod" else 0.46 * z
        rule = "arm_tip_pod" if name == "motor_pod" else "pod_top_prop"
        return [round(r * math.cos(a), 2), round(r * math.sin(a), 2), zf], [0, 0, 0], rule
    if name == "landing_gear":
        return [0, 0, 0], [0, 0, 0], "under_body"
    if name == "camera_mount":
        return [0, round(0.18 * y, 2), round(0.30 * z - 16, 2)], [0, 0, 0], "nose_under"
    if name == "battery_bay":
        return [0, 0, round(0.30 * z, 2)], [0, 0, 0], "lower_fuselage"
    if name == "controller_deck":
        return [0, 0, round(0.55 * z, 2)], [0, 0, 0], "fuselage_top"
    return [0, 0, 0], [0, 0, 0], "drone_default"


def _generic(graph: dict, env: dict) -> None:
    nodes = graph["nodes"]; n = len(nodes)
    cols = max(1, math.ceil(math.sqrt(n)))
    sx, sy = env["x"] / cols, env["y"] / cols
    for idx, node in enumerate(nodes):
        r, c = divmod(idx, cols)
        px = (c - (cols - 1) / 2) * sx
        py = (r - (cols - 1) / 2) * sy
        node["placement"] = {"translate_mm": [round(px, 2), round(py, 2), 0.0],
                             "rotate_deg": [0, 0, 0], "rule": "generic_grid"}


_DOMAIN = {"quadcopter drone": _drone_xform}


def resolve(graph: dict, design_spec: dict) -> dict:
    kind = design_spec.get("object_kind", "")
    env = graph["envelope_mm"]
    rule = _DOMAIN.get(kind)
    if rule:
        for node in graph["nodes"]:
            t, r, name = rule(node, env)
            node["placement"] = {"translate_mm": [round(v, 2) for v in t],
                                 "rotate_deg": [round(v, 2) for v in r], "rule": name}
        graph["placement_engine"] = f"domain:{kind}"
    else:
        _generic(graph, env)
        graph["placement_engine"] = "generic_layout"
    return graph
```

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Checkpoint** (`feat: deterministic placement rules`).

**Tests/verification:** pytest above.
**Rollback risk:** None — new isolated pure module.
**Success criteria:** Drone arms get 4 unique radial angles; generic objects get a filled, centered grid; `placement_engine` labelled.

---

## Phase 3 — Assembly composer (`assembly_composer.py`)

**Files:** Create `backend/app/orchestrator/assembly_composer.py`; Test `tests/test_assembly_composer.py`.

**Interfaces — Consumes:** resolved graph (Phase 2), `claude_code_adapter.workspace_dir/safe_workspace_path/ensure_workspace`. **Produces:**
- `emit_source(project_id: str, graph: dict) -> str` — returns build123d source string; each node `import_step('<ABS>')`, `.moved(Location(...))`, labelled, into `Compound(children=parts)`.
- `write_source(project_id: str, graph: dict) -> str` — writes the source to `<workspace>/output/generate.py`, returns it.

**Exact behavior change:** New module. Uses **absolute** STEP paths (resolved against the workspace) so the source runs under `cwd=project_dir`. Machine-written; trusted (not routed through the LLM safety gate — see Phase 6).

- [ ] **Step 1: Write failing test**
```python
# tests/test_assembly_composer.py
from app.orchestrator import assembly_graph, placement_rules, assembly_composer
from app.services import claude_code_adapter

MAN = {"project_id": "compose_t", "components": [
    {"name": "fuselage", "quantity": 1, "role": "body",
     "target_bbox_mm": {"x": 1, "y": 1, "z": 1}, "step": "output/components/fuselage/fuselage.step"},
    {"name": "arm", "quantity": 2, "role": "boom",
     "target_bbox_mm": {"x": 1, "y": 1, "z": 1}, "step": "output/components/arm/arm.step"},
]}
SPEC = {"project_id": "compose_t", "object_kind": "quadcopter drone",
        "object_class": "drone", "overall_envelope_mm": {"x": 250.0, "y": 250.0, "z": 120.0}}

def test_emit_source_uses_absolute_steps_and_compound():
    g = placement_rules.resolve(assembly_graph.build_graph(MAN, SPEC), SPEC)
    src = assembly_composer.emit_source("compose_t", g)
    ws = str(claude_code_adapter.workspace_dir("compose_t"))
    assert "def gen_step():" in src
    assert "from build123d import *" in src
    assert src.count("import_step(") == 3            # 1 fuselage + 2 arms
    assert ws in src                                 # absolute workspace path embedded
    assert "Compound(children=parts)" in src
    assert "os" not in src and "subprocess" not in src
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement**
```python
# backend/app/orchestrator/assembly_composer.py
"""Emit a deterministic build123d assembly from validated component STEPs. Zero Claude."""
from __future__ import annotations
from app.services import claude_code_adapter

_HEADER = "from build123d import *\n\n\ndef gen_step():\n    parts = []\n"


def emit_source(project_id: str, graph: dict) -> str:
    ws = claude_code_adapter.workspace_dir(project_id)
    lines = [_HEADER]
    for node in graph["nodes"]:
        abs_step = claude_code_adapter.safe_workspace_path(ws, node["step_file"])
        if abs_step is None:
            raise ValueError(f"unsafe/missing step path for {node['id']}: {node['step_file']}")
        p = node["placement"]; t = p["translate_mm"]; r = p["rotate_deg"]
        lines.append(
            f"    _p = import_step({str(abs_step)!r})\n"
            f"    _p = _p.moved(Location(({t[0]}, {t[1]}, {t[2]}), ({r[0]}, {r[1]}, {r[2]})))\n"
            f"    _p.label = {node['id']!r}\n"
            f"    parts.append(_p)\n"
        )
    lines.append("    return Compound(children=parts)\n")
    return "".join(lines)


def write_source(project_id: str, graph: dict) -> str:
    ws = claude_code_adapter.ensure_workspace(project_id)
    src = emit_source(project_id, graph)
    (ws / "output" / "generate.py").write_text(src)
    return src
```

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Checkpoint** (`feat: assembly composer (STEP import)`).

**Tests/verification:** pytest above. (Real STEP execution covered in Phase 8 integration.)
**Rollback risk:** None — new isolated module; not yet called.
**Success criteria:** Source defines `gen_step()`, imports one `import_step` per instance with absolute workspace paths, returns a `Compound`, contains no banned tokens.

---

## Phase 4 — Extend `validate_assembly`; drop Claude assembly prompts

**Files:** Modify `backend/app/orchestrator/assembly_builder.py`; Test `tests/test_assembly_validate.py`.

**Interfaces — Produces:** `validate_assembly(project_id, code, design_spec, graph=None) -> dict` (adds optional `graph` param + structural fields: `expected_nodes`, `node_count_ok`, `bbox_within_envelope`). Remove `assembly_prompt` and `repair_prompt`.

**Exact behavior change:** Keep the anti-primitive gate (reads `reports/inspection.txt`). When `graph` given, add: placed solids count vs `graph["node_count"]`, and overall bbox ≤ 1.5× envelope. `valid` also requires these when `graph` present. Delete the two prompt builders (no longer called).

- [ ] **Step 1: Write failing test** (uses a temp project with a hand-written `inspection.txt`)
```python
# tests/test_assembly_validate.py
import json
from app.orchestrator import assembly_builder
from app.core import paths

def _write_insp(pid, solids, faces, edges, bounds):
    d = paths.project_dir(pid) / "reports"; d.mkdir(parents=True, exist_ok=True)
    payload = {"tokens": [{"summary": {"shapeCount": solids, "faceCount": faces,
               "edgeCount": edges, "bounds": bounds}}]}
    (d / "inspection.txt").write_text(json.dumps(payload))

SPEC = {"complex": True, "explicit_primitive": False,
        "overall_envelope_mm": {"x": 250.0, "y": 250.0, "z": 120.0}}

def test_structural_node_count_mismatch_fails():
    pid = "valasm_t1"
    _write_insp(pid, solids=3, faces=100, edges=300,
                bounds={"min": [-100, -90, 0], "max": [100, 90, 100]})
    graph = {"node_count": 5}
    r = assembly_builder.validate_assembly(pid, "x" * 2000, SPEC, graph=graph)
    assert r["node_count_ok"] is False and r["valid"] is False

def test_valid_assembly_passes():
    pid = "valasm_t2"
    _write_insp(pid, solids=5, faces=100, edges=300,
                bounds={"min": [-100, -90, 0], "max": [100, 90, 100]})
    graph = {"node_count": 5}
    r = assembly_builder.validate_assembly(pid, "x" * 2000, SPEC, graph=graph)
    assert r["valid"] is True and r["node_count_ok"] and r["bbox_within_envelope"]
```

- [ ] **Step 2: Run, verify fail** (current `validate_assembly` has no `graph` param / fields).

- [ ] **Step 3: Implement** — replace `validate_assembly`, delete `assembly_prompt`/`repair_prompt`:
```python
# backend/app/orchestrator/assembly_builder.py  (full new body)
"""Assembly validation (deterministic). Composition lives in assembly_composer."""
from __future__ import annotations
import json
from app.core import paths


def validate_assembly(project_id: str, code: str, design_spec: dict,
                      graph: dict | None = None) -> dict:
    size = len(code.encode("utf-8"))
    solids = faces = edges = None
    bounds = {}
    insp = paths.project_dir(project_id) / "reports" / "inspection.txt"
    if insp.exists():
        try:
            s = json.loads(insp.read_text())["tokens"][0]["summary"]
            solids, faces, edges = s.get("shapeCount"), s.get("faceCount"), s.get("edgeCount")
            bounds = s.get("bounds") or {}
        except Exception:  # noqa: BLE001
            pass

    primitive_box = solids == 1 and faces == 6 and edges == 12
    complex_obj = design_spec.get("complex", True) and not design_spec.get("explicit_primitive", False)

    flags = []
    if complex_obj and size < 1200:
        flags.append("low_detail_output")
    if complex_obj and primitive_box:
        flags.append("primitive_box_output")
    if complex_obj and not (((solids or 0) > 1) or ((faces or 0) > 6)):
        flags.append("insufficient_complexity")

    node_count_ok = True
    bbox_within_envelope = True
    expected_nodes = None
    if graph is not None:
        expected_nodes = graph.get("node_count")
        node_count_ok = (solids or 0) >= (expected_nodes or 0)
        if not node_count_ok:
            flags.append("node_count_mismatch")
        env = design_spec.get("overall_envelope_mm") or {}
        mn, mx = bounds.get("min"), bounds.get("max")
        if env and mn and mx:
            dims = [mx[i] - mn[i] for i in range(3)]
            lim = [1.5 * env.get(k, 0) for k in ("x", "y", "z")]
            bbox_within_envelope = all(dims[i] <= lim[i] or lim[i] == 0 for i in range(3))
            if not bbox_within_envelope:
                flags.append("bbox_exceeds_envelope")

    valid = (not complex_obj) or (not flags)
    report = {
        "project_id": project_id, "valid": valid, "source_bytes": size,
        "solids": solids, "faces": faces, "edges": edges,
        "primitive_box_output": bool(complex_obj and primitive_box),
        "expected_nodes": expected_nodes, "node_count_ok": node_count_ok,
        "bbox_within_envelope": bbox_within_envelope, "flags": flags,
    }
    reports = paths.project_dir(project_id) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "assembly_validation.json").write_text(json.dumps(report, indent=2))
    return report
```

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Checkpoint** (`refactor: deterministic assembly validation; drop Claude assembly prompts`).

**Tests/verification:** pytest above.
**Rollback risk:** Medium — removes `assembly_prompt`/`repair_prompt`. Phase 6 removes their only callers in the same change set; if Phase 6 not yet applied, `claude_generation` import will break. Apply Phases 4 and 6 together, or land Phase 6 first. (Mitigation: do Phase 6 before running the full app.)
**Success criteria:** Node-count mismatch and oversized bbox fail; clean assembly passes; old prompt functions gone.

---

## Phase 5 — Claude result failure classification (`claude_code_adapter.py`)

**Files:** Modify `backend/app/services/claude_code_adapter.py`; Test `tests/test_failure_classifier.py`.

**Interfaces — Produces:** `classify_failure(*, is_error: bool, result_text: str|None, subtype: str|None, num_turns: int|None, max_turns: int) -> str|None` returning `None|"quota"|"turns"|"cad"`. `run_claude` return dict gains `"failure_class"`.

**Exact behavior change:** Add classifier. In `run_claude`, capture `subtype` and `num_turns` from the `result` stream object; include `failure_class` in the returned dict (`None` when ok).

- [ ] **Step 1: Write failing test**
```python
# tests/test_failure_classifier.py
from app.services.claude_code_adapter import classify_failure

def test_quota():
    assert classify_failure(is_error=True,
        result_text="You've hit your session limit · resets 12:30pm (UTC)",
        subtype="success", num_turns=1, max_turns=15) == "quota"

def test_turns():
    assert classify_failure(is_error=True, result_text="",
        subtype="error_max_turns", num_turns=16, max_turns=15) == "turns"

def test_cad_other():
    assert classify_failure(is_error=True, result_text="boom",
        subtype="error", num_turns=3, max_turns=15) == "cad"

def test_ok():
    assert classify_failure(is_error=False, result_text="done",
        subtype="success", num_turns=2, max_turns=15) is None
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement** — add classifier + wire into `run_claude`:
```python
# add near top-level functions in claude_code_adapter.py
def classify_failure(*, is_error: bool, result_text, subtype, num_turns, max_turns):
    if not is_error:
        return None
    t = (result_text or "").lower()
    if "session limit" in t or "hit your" in t or "usage limit" in t:
        return "quota"
    if subtype == "error_max_turns" or (num_turns is not None and num_turns >= max_turns):
        return "turns"
    return "cad"
```
In `_read_stream`, also capture subtype/num_turns from the result object:
```python
                if obj.get("type") == "result":
                    is_error = bool(obj.get("is_error"))
                    result_text = obj.get("result")
                    result_subtype = obj.get("subtype")        # NEW (nonlocal)
                    result_num_turns = obj.get("num_turns")    # NEW (nonlocal)
                    session_id = obj.get("session_id") or session_id
```
(declare `result_subtype = None`, `result_num_turns = None` alongside the other nonlocals). In the success/return path, compute and include:
```python
        max_turns_used = max_turns or config.CLAUDE_CODE_MAX_TURNS
        failure_class = classify_failure(
            is_error=is_error, result_text=result_text, subtype=result_subtype,
            num_turns=result_num_turns, max_turns=max_turns_used)
        ok = exit_code == 0 and not is_error
        return {"ok": ok, "session_id": session_id, "result_text": result_text,
                "exit_code": exit_code, "failure_class": failure_class,
                "error": None if ok else (f"claude exit {exit_code}, is_error={is_error}")}
```
Also add `"failure_class": None` to the early-return dicts (binary-not-found, cancelled, timeout → use `"cad"` for timeout, `None` for cancelled).

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Checkpoint** (`feat: classify Claude failures (quota/turns/cad)`).

**Tests/verification:** pytest above.
**Rollback risk:** Low — additive field; existing callers ignore `failure_class` until Phase 6.
**Success criteria:** Classifier returns correct class for quota/turns/cad/ok; `run_claude` returns `failure_class`.

---

## Phase 6 — Wire failure classes + deterministic assembly into `claude_generation.py`

**Files:** Modify `backend/app/services/claude_generation.py`; Test `tests/test_claude_generation_wiring.py`.

**Interfaces — Consumes:** Phases 1–5. Uses `assembly_graph`, `placement_rules`, `assembly_composer`, `assembly_builder.validate_assembly`, `run_claude` `failure_class`.

**Exact behavior changes:**
1. `fail()` gains a `status` arg (default `"FAILED"` for back-compat) so it can set `FAILED_CAD/QUOTA/TURNS`.
2. `_claude_call` returns `failure_class`; component loop maps it: `quota` → `fail("COMPONENT_GENERATION", …, status="FAILED_QUOTA")` and **return immediately** (no repair); `turns` → `FAILED_TURNS` return; `cad`/validation → existing repair loop; on exhaustion `fail(…, status="FAILED_CAD")`.
3. Replace the assembly loop (old lines ~326–367) with deterministic stage:
```python
            # ---- 5-6. Deterministic assembly (no Claude) ----
            if job:
                job.stage = "ASSEMBLY_GENERATION"; job_service.save_job(job)
            graph = assembly_graph.build_graph(manifest, design_spec)
            placement_rules.resolve(graph, design_spec)
            assembly_graph.write_graph(project_id, graph)
            assembly_composer.write_source(project_id, graph)
            code, err = _read_generate_py(project_id)
            if code is None:
                await fail("ASSEMBLY_GENERATION", err or "composer wrote no source",
                           status="FAILED_CAD"); return
            await ch.publish(SOURCE_WORKER, "cad.execution.started", stage="cad_execution",
                             message=f"Composing assembly ({graph['node_count']} parts, "
                                     f"{graph['placement_engine']})")
            if job:
                job.stage = "ASSEMBLY_EXECUTION"; job_service.save_job(job)
            cad = await _run_cad_trusted(project_id, code)
            if not cad["ok"]:
                await fail("ASSEMBLY_EXECUTION", cad.get("error", "assembly CAD failed"),
                           status="FAILED_CAD"); return
            av = assembly_builder.validate_assembly(project_id, code, design_spec, graph=graph)
            if not av["valid"]:
                await fail("ASSEMBLY_VALIDATION", ", ".join(av["flags"]) or "assembly invalid",
                           status="FAILED_CAD"); return
            await ch.publish(SOURCE_WORKER, "cad.execution.completed", stage="cad_execution",
                             message=f"Assembly valid (solids={av['solids']}, faces={av['faces']})")
```
4. Add `_run_cad_trusted` — same as `_run_cad` but **skips** `check_code_safety` (machine-generated source is trusted; `import_step` is legitimate and must not be blocked):
```python
async def _run_cad_trusted(project_id: str, code: str) -> dict:
    def _work() -> dict:
        cad_runner.generate_source_from_llm(project_id, code)
        step = cad_runner.export_step(project_id)
        if not step["ok"]:
            return {"ok": False, "error": step.get("stderr") or "STEP export failed"}
        cad_runner.export_meshes(project_id)
        cad_runner.inspect_step(project_id)
        cad_runner.generate_snapshot(project_id)
        return {"ok": True}
    return await asyncio.to_thread(_work)
```
5. Add imports: `assembly_graph`, `placement_rules`, `assembly_composer` (keep `assembly_builder`). Remove now-unused single-shot assembly references.

- [ ] **Step 1: Write failing test** (mock Claude so assembly stage makes no real call; assert quota abort + no-Claude assembly):
```python
# tests/test_claude_generation_wiring.py
import asyncio, json, types
import pytest
from app.services import claude_generation as cg, claude_code_adapter, job_service
from app.core import paths

def _seed_project(pid, kind="quadcopter drone"):
    paths.ensure_project_skeleton(pid)
    (paths.project_dir(pid) / "brief.json").write_text(json.dumps({
        "project_id": pid, "prompt": "create a 3D drone", "intent": "concept_cad",
        "parameters": {"dimensions": "250 x 250 x 120 mm", "units": "mm", "material": "PLA"},
        "user_answers": {"dimensions": "250 x 250 x 120 mm"}, "ready_to_generate": True,
        "generation_mode": "qwen_claude_code"}))

def test_quota_aborts_without_repair(monkeypatch):
    pid = "wire_quota"; _seed_project(pid)
    job = job_service.create_job_full(pid, "generation", "CREATED")
    calls = {"n": 0}
    async def fake_run_claude(project_id, job_id, prompt, ch, **kw):
        calls["n"] += 1
        return {"ok": False, "failure_class": "quota", "error": "session limit",
                "session_id": None, "result_text": "You've hit your session limit",
                "exit_code": 1}
    monkeypatch.setattr(claude_code_adapter, "run_claude", fake_run_claude)
    asyncio.run(cg.run(pid, job.job_id))
    j = job_service.get_job(job.job_id)
    assert j.status == "FAILED_QUOTA"
    assert calls["n"] == 1   # aborted on first component, no repair spin
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** the changes above (fail() status arg, _claude_call failure_class, component-loop mapping, deterministic assembly block, `_run_cad_trusted`, imports).
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Checkpoint** (`feat: deterministic assembly stage + failure-class wiring`).

**Tests/verification:** pytest above + Phase 8 integration.
**Rollback risk:** **High** — core pipeline edit. Keep the old assembly block in a `git stash`/backup copy; the wiring test + Phase 8 drone integration are the gates. The single-shot branch still exists after this phase (removed in Phase 7), so simple parts are unaffected here.
**Success criteria:** Quota fails fast as `FAILED_QUOTA` with one call; complex objects reach the deterministic assembly stage with zero assembly Claude calls.

---

## Phase 7 — Route simple parts through the 1-node graph; remove single-shot branch

**Files:** Modify `backend/app/services/claude_generation.py`; Test `tests/test_simple_part_path.py`.

**Exact behavior change:** Force every object through the hierarchical branch. For `manifest["component_count"] < 2`, synthesize a 1-component manifest from the work order so there is exactly one component node, then run the same component-generate → validate → deterministic-assemble (1-part Compound) → export path. Delete the `else:` single-shot block (old ~369–415) and the `hierarchical` guard (always hierarchical).

Implementation detail — when `component_count < 2`, build a single component entry:
```python
        if manifest["component_count"] < 2:
            only = {"name": "part", "quantity": 1,
                    "role": design_spec.get("object_kind", "part"),
                    "target_bbox_mm": {  # from envelope
                        "x": design_spec["overall_envelope_mm"]["x"],
                        "y": design_spec["overall_envelope_mm"]["y"],
                        "z": design_spec["overall_envelope_mm"]["z"]},
                    "source": "output/components/part/generate.py",
                    "step": "output/components/part/part.step", "status": "pending"}
            manifest = {**manifest, "component_count": 1, "components": [only]}
        # (always run the hierarchical component loop + deterministic assembly below)
```
A 1-node assembly still goes through `assembly_composer` (one `import_step`, identity-ish placement via generic/`drone_default`) → a valid single-solid Compound. `validate_assembly` with `graph.node_count==1` and the anti-primitive gate: a genuine detailed single part has faces>6 so passes; only a literal box fails (acceptable — matches existing primitive policy for non-primitive intents).

- [ ] **Step 1: Write failing test** (simple part: mock Claude to write a valid 1-component source; assert no single-shot path, assembly graph has 1 node):
```python
# tests/test_simple_part_path.py
import asyncio, json
from app.services import claude_generation as cg, claude_code_adapter, job_service
from app.core import paths

VALID_PART = ("from build123d import *\n\n"
              "def gen_step():\n"
              "    return fillet(Box(50,50,10).edges(), 2)\n")

def test_simple_part_uses_one_node_graph(monkeypatch):
    pid = "wire_simple"
    paths.ensure_project_skeleton(pid)
    (paths.project_dir(pid) / "brief.json").write_text(json.dumps({
        "project_id": pid, "prompt": "create a 3D mounting plate", "intent": "concept_cad",
        "parameters": {"dimensions": "120 x 80 x 6 mm", "units": "mm", "material": "PLA"},
        "user_answers": {"dimensions": "120 x 80 x 6 mm"}, "ready_to_generate": True,
        "generation_mode": "qwen_claude_code"}))
    job = job_service.create_job_full(pid, "generation", "CREATED")
    async def fake_run_claude(project_id, job_id, prompt, ch, **kw):
        # emulate Claude writing the single component source
        ws = claude_code_adapter.ensure_workspace(project_id)
        d = ws / "output" / "components" / "part"; d.mkdir(parents=True, exist_ok=True)
        (d / "generate.py").write_text(VALID_PART)
        return {"ok": True, "failure_class": None, "session_id": "s",
                "result_text": "done", "exit_code": 0, "error": None}
    monkeypatch.setattr(claude_code_adapter, "run_claude", fake_run_claude)
    asyncio.run(cg.run(pid, job.job_id))
    g = json.loads((paths.project_dir(pid) / "reports" / "assembly_graph.json").read_text())
    assert g["node_count"] == 1
    j = job_service.get_job(job.job_id)
    assert j.status == "COMPLETED"
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** the 1-node routing; delete the single-shot `else` block and `hierarchical` conditional.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Checkpoint** (`refactor: route simple parts through 1-node graph; remove single-shot`).

**Tests/verification:** pytest above.
**Rollback risk:** **High** — removes the single-shot path entirely. Gate with this test + Phase 8 calibration-block/mounting-plate mini-benchmark. Back up the removed block.
**Success criteria:** Simple part produces a 1-node `assembly_graph.json` and `COMPLETED`; no single-shot code path remains.

---

## Phase 8 — Verification (the required closing phases)

> Real CAD + (limited) real Claude. Run when component STEPs / Claude quota are available. Each sub-phase is its own gate.

### 8.1 Unit tests (all new + existing)
- [ ] Run: `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_assembly_graph.py tests/test_placement_rules.py tests/test_assembly_composer.py tests/test_assembly_validate.py tests/test_failure_classifier.py tests/test_claude_generation_wiring.py tests/test_simple_part_path.py tests/test_orchestrator.py tests/test_claude_code.py -v`
- **Success:** all pass. **Rollback risk:** none (read-only run). **Files:** none.

### 8.2 Drone regression (deterministic assembly on real STEPs, no Claude)
- [ ] Seed a project workspace with the 8 validated drone component STEPs (reuse `runs/8330a2…/claude-workspace/output/components/*` — copy the 7 good + the repaired controller_deck) into a fresh `pid`. Build graph → resolve → write graph → compose → `_run_cad_trusted` → `validate_assembly`.
- [ ] Verify script: `assembly_graph.json` has `node_count == 17`; `cad/model.step` exists; `assembly_validation.json` `valid == true`, `solids ≈ 17`, `faces > 6`, `bbox_within_envelope == true`; and **assert `run_claude` is never called** in this stage.
- **Success:** assembly valid, 17 solids, zero Claude calls. **Rollback risk:** none (scratch project). **Files:** scratch only; clean up after.

### 8.3 Gear housing regression (the previously-passing object)
- [ ] Re-run gear housing end-to-end through `cg.run()` (real component Claude calls + deterministic assembly). 
- [ ] Verify: `status == COMPLETED`, `assembly_validation.json valid`, `assembly_graph.json` present, geometry non-primitive.
- **Success:** still completes; now also emits `assembly_graph.json`. **Rollback risk:** uses Claude quota (small, 8 component calls). **Files:** scratch project.

### 8.4 Mini benchmark rerun (4 objects)
- [ ] Reuse `scratchpad/benchmark.py` limited to: calibration block, mounting plate, quadcopter drone, gear housing. Run sequentially (background; checkpoint per object). Capture pass/status/time/component-pass/repairs/assembly/solids/faces/edges/bbox + the new `failure_class`/status.
- [ ] **Expected deltas vs 2026-06-19:** calibration block + mounting plate now reach the 1-node assembly and `COMPLETED` (previously `FAILED` on `error_max_turns`); drone reaches deterministic assembly (no `max_turns` on assembly) → expect `COMPLETED` or `FAILED_CAD` (geometry), never `FAILED_TURNS`; gear housing still `COMPLETED`.
- **Success:** zero `error_max_turns`/`FAILED_TURNS` on these 4; ≥3/4 `COMPLETED`. **Rollback risk:** Claude quota over ~ up to 3 objects' component calls — run when quota is fresh (benchmark showed ~9 substantive generations before the cap). **Files:** scratch projects.

### 8.5 Update `benchmark_summary.md`
- [ ] Append a "Post-refactor (2026-06-21) — deterministic assembly" section: 4-object mini-benchmark table, before/after for the 4, and confirmation that the two monolithic failure modes (single-shot `error_max_turns`, monolithic-assembly `error_max_turns`) are eliminated; note quota now surfaces as `FAILED_QUOTA` (abort-fast) rather than 24-attempt spin.
- **Success:** report reflects measured post-refactor results. **Rollback risk:** none (doc). **Files:** `benchmark_summary.md`.

---

## Self-review

**Spec coverage:** STEP-import reuse (Phase 3, abs paths) ✓; no `gen_step()` re-exec (composer imports STEPs only) ✓; `assembly_graph.json` with node ids/parent/component_type/placement (Phase 1+2) ✓; FAILED_CAD/QUOTA/TURNS + quota abort-fast (Phase 5+6) ✓; deterministic placement domain+generic (Phase 2) ✓; extended assembly validation (Phase 4) ✓; remove monolithic single-shot + assembly Claude calls (Phase 6+7) ✓; required closing phases 1–5 (Phase 8) ✓; no frontend/Qwen/CAD-prompt changes (Global Constraints) ✓.
**Placeholder scan:** none — every code step has full code.
**Type consistency:** `build_graph→dict`, `resolve(graph, spec)→dict` (mutates+returns), `emit_source(project_id, graph)→str`, `write_source(project_id, graph)→str`, `validate_assembly(pid, code, spec, graph=None)→dict`, `classify_failure(...)→str|None`, `run_claude` adds `failure_class`. Consistent across phases.

**Known sequencing constraint:** Phases 4 and 6 must land together (Phase 4 deletes `assembly_prompt`; Phase 6 removes its caller). Implement 5 → 6 → 4 → 7, or 4+6 as one reviewed unit.
