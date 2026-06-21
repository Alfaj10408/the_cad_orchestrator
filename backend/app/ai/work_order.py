"""Repo-skill-aware Qwen work-order builder.

Turns an engineering brief into a complete, structured CAD work order that
follows the installed `cad` skill workflow exactly (STEP-first build123d:
brief -> plan -> gen_step() -> STEP export from source -> inspect
refs/facts/planes/positioning -> mandatory snapshot -> secondary STL/3MF/GLB ->
CAD Viewer handoff -> targeted repair). Emits both the structured work order
and the natural-language Claude Code prompt.

Mirrors repo/text-to-cad/skills/cad/SKILL.md. Does not execute tools.
"""
from __future__ import annotations

from app.ai import cad_agent

COORD_CONVENTION = (
    "millimeters; origin at the center of the main part/assembly; XY base plane; "
    "+Z up; closed positive-volume solids"
)

# The cad skill's required workflow, as an explicit ordered sequence.
SKILL_SEQUENCE = [
    "Use the installed cad skill (STEP-first build123d).",
    "Classify the task (new assembly vs part vs modification).",
    "Write a natural-language CAD brief (dimensions, units, features, assumptions, "
    "output paths, validation targets) — do not ask for JSON.",
    "Plan before coding: named parameters, labels, source paths, expected bounding "
    "boxes, and mating/positioning datums.",
    "Author build123d Python source defining gen_step() with named parameters and "
    "closed positive-volume solids; for assemblies use labeled compounds / "
    "build123d joints and source-level part relationships.",
    "Generate STEP from the source generator (scripts/step on the generator), not "
    "from generated artifacts. Generate explicit targets only (no directory-wide).",
    "Validate geometry: scripts/inspect refs --facts --planes --positioning, then "
    "targeted measure/mate/frame/diff as needed.",
    "Snapshot is MANDATORY: run scripts/snapshot on the primary STEP after export.",
    "Export STL/3MF/GLB only as secondary outputs after STEP.",
    "Hand the artifact path(s) to the cad-viewer skill and return live link(s).",
    "If a check fails, repair the smallest responsible source section and rerun the "
    "failed checks only.",
]

ROBOTICS_FUTURE_PIPELINE = [
    "CAD (this stage)",
    "partitioned parts",
    "URDF (not executed yet)",
    "SRDF (not executed yet)",
    "optional G-code / Bambu (not executed yet)",
]


def _expected_geometry(fp: dict) -> dict:
    if not fp["complex"]:
        return {
            "single_solid_ok": True,
            "note": "user requested a primitive; a clean single solid is acceptable",
        }
    return {
        "not_a_single_box": True,
        "multiple_named_components": True,
        "fourfold_symmetry": fp["object_kind"].startswith("quadcopter"),
        "visible_silhouette": f"recognizable {fp['object_kind']} silhouette",
        "urdf_ready_separation": "separate named components where possible",
    }


def _validation_plan(fp: dict) -> dict:
    if not fp["complex"]:
        return {
            "source_min_bytes": 0,
            "checks": ["closed positive-volume solid", "STEP export succeeds",
                       "inspection facts present", "snapshot rendered"],
        }
    return {
        "source_min_bytes": 1200,
        "reject_primitive_box": "not 1 solid / 6 faces / 12 edges",
        "min_named_feature_mentions_in_source": 8,
        "inspection": "must show more complexity than a primitive box "
                      "(shapeCount>1 or faceCount>6)",
        "snapshot": f"must visually show a {fp['object_kind']}-like layout",
        "tool_checks": ["scripts/inspect refs --facts --planes --positioning",
                        "scripts/snapshot on primary STEP"],
    }


def _build123d_strategy(fp: dict) -> list[str]:
    if not fp["complex"]:
        return ["single named-parameter solid in gen_step()"]
    return [
        "named dimension parameters near the top (mm)",
        "build each component as its own labeled solid/part",
        "position components with explicit Location transforms / part-local datums",
        "boolean-union or assemble into a single labeled assembly Compound",
        "gen_step() returns the assembly Compound",
        "apply fillets/chamfers per component for a realistic, manufacturable look",
    ]


def build(brief: dict, project_id: str) -> dict:
    """Return the full structured work order (18 fields)."""
    fp = cad_agent.build_feature_plan(brief)
    params = brief.get("parameters", {}) or {}
    answers = brief.get("user_answers", {}) or {}
    intent = brief.get("intent", "")
    object_class = (
        fp["object_kind"] + " assembly" if fp["complex"] else fp["object_kind"]
    )

    components = fp["features"] if fp["complex"] else ["single block"]
    features = list(components)
    if fp["complex"]:
        features += [
            "fourfold / mirrored symmetry where applicable",
            "named mating datums between components",
            "fillets and chamfers on exposed edges",
            "fastener / mount bores where functional",
        ]

    output_paths = {
        "source": "output/generate.py",
        "step": "artifacts/model.step (PRIMARY)",
        "stl": "artifacts/model.stl (secondary)",
        "3mf": "artifacts/model.3mf (secondary)",
        "glb": "artifacts/model.glb (secondary)",
        "snapshot": "artifacts/snapshot.png (mandatory)",
        "summary": "output/generation_summary.json",
        "manifest": "output/artifact_manifest.json",
    }

    wo: dict = {
        "user_prompt": brief.get("prompt", ""),
        "object_class": object_class,
        "intent": intent,
        "dimensions": params.get("dimensions") or answers.get("dimensions") or "choose sensible defaults",
        "units": params.get("units") or answers.get("units") or "mm",
        "material": params.get("material") or answers.get("material") or "PLA",
        "coordinate_convention": COORD_CONVENTION,
        "required_components": components,
        "required_features": features,
        "expected_geometry": _expected_geometry(fp),
        "build123d_strategy": _build123d_strategy(fp),
        "required_skill_sequence": SKILL_SEQUENCE,
        "output_paths": output_paths,
        "validation_plan": _validation_plan(fp),
        "snapshot_plan": {
            "mandatory": True,
            "when": "after STEP export, on the primary STEP artifact",
            "tool": "scripts/snapshot",
        },
        "viewer_handoff": {
            "skill": "cad-viewer",
            "action": "hand primary STEP + secondary artifacts to the cad-viewer "
                      "skill and return live link(s)",
        },
        "repair_policy": {
            "strategy": "change the smallest responsible source section, regenerate, "
                        "rerun only the failed checks",
            "max_attempts": 2,
        },
        "_meta": {"complex": fp["complex"], "explicit_primitive": fp["explicit_primitive"],
                  "object_kind": fp["object_kind"]},
    }

    if intent in ("robotics_urdf", "mechatronic"):
        wo["future_pipeline"] = ROBOTICS_FUTURE_PIPELINE

    wo["claude_code_prompt"] = render_claude_prompt(wo, brief)
    return wo


def render_claude_prompt(wo: dict, brief: dict) -> str:
    """Render the natural-language Claude Code prompt from the work order.

    MUST start with 'Use the installed cad skill.'
    """
    fp_complex = wo["_meta"]["complex"]
    explicit_primitive = wo["_meta"]["explicit_primitive"]
    object_class = wo["object_class"]
    comp_lines = "\n".join(f"  {i}. {c}" for i, c in enumerate(wo["required_components"], 1))
    seq_lines = "\n".join(f"  {i}. {s}" for i, s in enumerate(wo["required_skill_sequence"], 1))
    plan = brief.get("plan") or brief.get("summary") or ""

    reject = (
        "Do NOT output a placeholder or a single Box(...)/cube. A primitive "
        "block-only result is REJECTED and will be sent back for rewrite."
        if not explicit_primitive else
        "A clean single primitive solid is acceptable (the user asked for one)."
    )

    val = wo["validation_plan"]
    val_text = (
        f"- source/model.py (your output/generate.py) > {val.get('source_min_bytes', 0)} bytes\n"
        f"- {val.get('reject_primitive_box', 'valid closed solid')}\n"
        f"- at least {val.get('min_named_feature_mentions_in_source', 1)} named feature "
        "labels/parameters in the source\n"
        f"- inspection: {val.get('inspection', 'facts present')}\n"
        f"- snapshot: {val.get('snapshot', 'rendered')}"
        if fp_complex else
        "- valid closed positive-volume solid; STEP export, inspection, snapshot succeed"
    )

    fut = ""
    if "future_pipeline" in wo:
        fut = (
            "\nThis is a robotics/mechatronic object. Keep components separated so they "
            "are URDF-ready. Future stages (NOT now): "
            + " -> ".join(wo["future_pipeline"]) + ".\n"
        )

    return f"""Use the installed cad skill.

Build a STEP-first build123d CAD model of {object_class}. STEP is the primary CAD
artifact; STL/3MF/GLB are secondary outputs produced only after STEP.

Read the full work order at input/project_spec.json first.

Follow the cad skill workflow exactly:
{seq_lines}

1) CAD BRIEF (write it first, in natural language): restate the object, its
   {wo['dimensions']} ({wo['units']}) envelope, material {wo['material']}, the
   coordinate convention ({wo['coordinate_convention']}), the components, and the
   validation targets. Do not ask for JSON.

2) PLAN BEFORE CODING: list named parameters, component labels, expected bounding
   boxes per component, and the mating/positioning datums between components.

3) AUTHORING — write output/generate.py defining exactly `def gen_step():`:
{chr(10).join('   - ' + s for s in wo['build123d_strategy'])}
   - Start with: from build123d import *
   - Named parameters in millimeters near the top.
   - Closed, positive-volume solids; labeled assembly compound for assemblies.
   - No file/network I/O; no os/subprocess/socket/shutil/pathlib/requests,
     no open()/eval()/exec()/__import__. Do NOT execute the code yourself.

REQUIRED COMPONENTS (build ALL, as separate labeled parts where possible):
{comp_lines}

EXPECTED GEOMETRY: recognizable {wo['_meta']['object_kind']}; multiple named
components; symmetry where natural; URDF-ready separation. {reject}

The backend then runs the skill tools on your generator: STEP export from source
(scripts/step on the generator), inspection (scripts/inspect refs --facts --planes
--positioning), a MANDATORY snapshot (scripts/snapshot) after STEP, then secondary
STL/3MF/GLB, then CAD Viewer handoff. Author the model so every one of those passes.

Also write output/generation_summary.json and output/artifact_manifest.json listing
artifacts/model.step (primary) plus stl/glb/snapshot (secondary).

VALIDATION TARGETS:
{val_text}

REPAIR POLICY: {wo['repair_policy']['strategy']}.
{fut}
Engineering plan from the Qwen planner:
{plan}
"""
