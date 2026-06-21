"""Async qwen_claude_code generation pipeline.

Sequence: Qwen plan -> project_spec.json -> Claude Code writes build123d
gen_step() source -> existing CAD worker executes it -> validate -> bounded
Claude repair loop -> artifacts. Every stage streams normalized events.

Claude only writes code; the backend owns all CAD execution. Reuses the
existing cad_runner / report_service / artifact_service pipeline.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from app.ai import cad_agent, work_order
from app.core import config, paths
from app.orchestrator import (
    assembly_builder,
    assembly_composer,
    assembly_graph,
    component_planner,
    component_validator,
    placement_rules,
)
from app.schemas.events import (
    SOURCE_ARTIFACT,
    SOURCE_QWEN,
    SOURCE_SYSTEM,
    SOURCE_WORKER,
)
from app.services import (
    artifact_service,
    cad_runner,
    claude_code_adapter,
    event_service,
    job_service,
    llm_cad_generator,
    report_service,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_brief(project_id: str) -> dict:
    p = paths.project_dir(project_id) / "brief.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _build_claude_prompt(spec: dict, repair_error: str | None = None) -> str:
    """Detailed, cad-skill-aware Claude task prompt (demo-quality CAD)."""
    object_kind = spec.get("object_kind") or "the requested object"
    features = spec.get("features") or []
    min_feat = spec.get("min_feature_count", 8)
    complex_obj = spec.get("complex", True)
    explicit_primitive = spec.get("explicit_primitive", False)
    feat_lines = "\n".join(f"  {i}. {f}" for i, f in enumerate(features, 1))

    reject_clause = (
        "DO NOT output a placeholder or a single Box(...)/cube. A primitive "
        "block-only result is REJECTED and will be sent back for rewrite."
        if not explicit_primitive
        else "A simple primitive block is acceptable here (the user asked for one)."
    )

    base = f"""You are a CAD engineer. The installed `cad` skill is available — use its
STEP-first build123d conventions to create a detailed CAD model of {object_kind}.

Read the full specification at input/project_spec.json first.

DELIVERABLE — write these files inside your workspace ONLY:
- output/generate.py            build123d source defining exactly `def gen_step():`
                                returning ONE build123d assembly Compound (or Solid).
- output/generation_summary.json   {{"status":"completed","generator":"claude_code",
                                "planner":"qwen","project_id":"{spec.get('project_id','')}",
                                "files_created":["output/generate.py"],"assumptions":[],"warnings":[]}}
- output/artifact_manifest.json    {{"artifacts":[{{"type":"step","path":"artifacts/model.step"}},
                                {{"type":"stl","path":"artifacts/model.stl"}},
                                {{"type":"glb","path":"artifacts/model.glb"}},
                                {{"type":"preview","path":"artifacts/snapshot.png"}}]}}

The backend will execute gen_step() to generate STEP, then STL and GLB, render a
snapshot PNG, and run CAD inspection. Write the model so ALL of those succeed.

build123d source requirements:
- Start with: from build123d import *
- Named dimension variables in millimeters near the top.
- Origin at part center, XY base plane, +Z up; units are millimeters.
- Every solid CLOSED and POSITIVE-VOLUME. Build separate NAMED components where
  possible (future URDF-ready links/joints), then boolean-union them into a single
  assembly Compound returned by gen_step().
- Do NOT read/write files; no os/subprocess/socket/shutil/pathlib/requests,
  no open()/eval()/exec()/__import__. Do NOT execute the code yourself.

QUALITY BAR — demo-quality, recognizable:
- The model MUST visibly look like {object_kind}, not an abstract block.
- Implement ALL of these named features (at least {min_feat}):
{feat_lines}
- Manufacturability: sensible wall thickness, fillets/chamfers where natural,
  3D-print friendly, no zero-thickness faces.
- {reject_clause}

Dimensions: {spec.get('dimensions', 'choose sensible')} ({spec.get('units', 'mm')}).
Material: {spec.get('material', 'PLA')}.

Engineering plan from the Qwen planner:
{spec.get('plan', '')}
"""
    if not complex_obj:
        base += "\n(This is a simple part — a clean single solid is fine.)\n"
    if repair_error:
        base += (
            "\n--- REWRITE REQUIRED ---\n"
            f"{repair_error}\n"
            "Rewrite output/generate.py to fully satisfy the named features above "
            "and produce a recognizable, closed, positive-volume assembly.\n"
        )
    return base


async def _run_cad(project_id: str, code: str) -> dict:
    """Validate + run the existing deterministic CAD export pipeline (in thread)."""
    ok, reason = llm_cad_generator.check_code_safety(code)
    if not ok:
        return {"ok": False, "stage": "validation", "error": reason}

    def _work() -> dict:
        cad_runner.generate_source_from_llm(project_id, code)
        step = cad_runner.export_step(project_id)
        if not step["ok"]:
            return {"ok": False, "stage": "step", "error": step.get("stderr") or "STEP export failed"}
        meshes = cad_runner.export_meshes(project_id)
        insp = cad_runner.inspect_step(project_id)
        snap = cad_runner.generate_snapshot(project_id)
        return {"ok": True, "step": step, "meshes": meshes, "insp": insp, "snap": snap}

    return await asyncio.to_thread(_work)


async def _run_cad_trusted(project_id: str, code: str) -> dict:
    """Run CAD export pipeline for machine-generated source.

    Returns only ``{"ok": bool, "error"?: str}`` — no step/meshes/insp/snap sub-keys.
    Skips check_code_safety because the source is machine-generated/trusted
    (import_step is legitimate in composer output and must not be blocked).
    """
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


def _quality_gate(project_id: str, code: str, spec: dict) -> dict:
    """Post-generation detail check. Flags low_detail / primitive_box output."""
    size = len(code.encode("utf-8"))
    low_detail = size < 1200

    solids = faces = edges = None
    insp = paths.project_dir(project_id) / "reports" / "inspection.txt"
    if insp.exists():
        try:
            s = json.loads(insp.read_text())["tokens"][0]["summary"]
            solids, faces, edges = s.get("shapeCount"), s.get("faceCount"), s.get("edgeCount")
        except Exception:  # noqa: BLE001
            pass

    is_box = solids == 1 and faces == 6 and edges == 12
    complex_obj = spec.get("complex", True) and not spec.get("explicit_primitive", False)
    primitive_box_output = bool(is_box and complex_obj)

    flags = []
    if low_detail and complex_obj:
        flags.append("low_detail_output")
    if primitive_box_output:
        flags.append("primitive_box_output")

    result = {
        "source_bytes": size, "solids": solids, "faces": faces, "edges": edges,
        "low_detail_output": low_detail and complex_obj,
        "primitive_box_output": primitive_box_output, "flags": flags,
    }
    try:
        (paths.project_dir(project_id) / "reports" / "quality.json").write_text(
            json.dumps(result, indent=2)
        )
    except Exception:  # noqa: BLE001
        pass
    return result


def _read_generate_py(project_id: str) -> tuple[str | None, str | None]:
    ws = claude_code_adapter.workspace_dir(project_id)
    target = claude_code_adapter.safe_workspace_path(ws, "output/generate.py")
    if target is None or not target.is_file():
        return None, "Claude did not produce a safe output/generate.py"
    return target.read_text(), None


async def run(project_id: str, job_id: str) -> None:
    """Full async pipeline for one qwen_claude_code job. Streams events."""
    ch = event_service.get_channel(project_id, job_id)
    job = job_service.get_job(job_id)

    async def fail(stage: str, detail: str, status: str = "FAILED") -> None:
        if job:
            job.status = status
            job.stage = stage
            job.completed_at = _now()
            job_service.save_job(job)
        await ch.publish(SOURCE_SYSTEM, "job.failed", stage=stage, message=detail)

    try:
        if job:
            job.status = "RUNNING"
            job.started_at = _now()
            job.stage = "PLANNING"
            job_service.save_job(job)
        await ch.publish(SOURCE_SYSTEM, "job.started", stage="planning",
                         message="Generation started (qwen_claude_code)")

        # ---- 1. Qwen plan ----
        await ch.publish(SOURCE_QWEN, "planner.started", stage="planning",
                         message="Qwen is preparing the CAD specification")
        brief = _load_brief(project_id)
        plan_text, plan_source = await asyncio.to_thread(cad_agent.build_worker_prompt, brief)
        brief["plan"] = plan_text

        # Repo-skill-aware structured work order (18 fields) + Claude prompt.
        wo = work_order.build(brief, project_id)
        claude_prompt = wo["claude_code_prompt"]

        # Persist the work order + the exact Claude prompt for review/debug.
        reports = paths.project_dir(project_id) / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "qwen_work_order.json").write_text(json.dumps(wo, indent=2))
        (reports / "claude_code_prompt.txt").write_text(claude_prompt)

        # Claude reads the full work order from its sandbox input.
        ws = claude_code_adapter.ensure_workspace(project_id)
        (ws / "input" / "project_spec.json").write_text(json.dumps(wo, indent=2))

        # Quality-gate spec view (small subset used by _quality_gate).
        meta = wo["_meta"]
        spec = {
            "complex": meta["complex"],
            "explicit_primitive": meta["explicit_primitive"],
            "object_kind": meta["object_kind"],
            "features": wo["required_components"],
        }

        await ch.publish(SOURCE_QWEN, "planner.delta", stage="planning",
                         message=f"Components: {', '.join(wo['required_components'][:8])}")
        await ch.publish(SOURCE_QWEN, "planner.completed", stage="planning",
                         message=f"Work order ready: {wo['object_class']} with "
                                 f"{len(wo['required_components'])} components, "
                                 f"skill-driven STEP-first plan ({plan_source})")

        if job:
            job.stage = "CLAUDE_CODE_GENERATION"
            job_service.save_job(job)

        # ---- 2. Decompose: engineering spec + component manifest ----
        design_spec = component_planner.build_design_spec(project_id, brief, wo)
        manifest = component_planner.build_component_manifest(project_id, brief, design_spec)
        hierarchical = design_spec["complex"] and manifest["component_count"] >= 2
        await ch.publish(
            SOURCE_QWEN, "planner.delta", stage="planning",
            message=(f"Decomposed into {manifest['component_count']} components: "
                     + ", ".join(c["name"] for c in manifest["components"])) if hierarchical
            else "Simple part — single-shot generation",
        )

        async def _claude_call(prompt: str) -> tuple[str, dict]:
            """One Claude call. Returns (status, res); status: ok|fail|cancel.

            Also surfaces res['failure_class']: None | 'quota' | 'turns' | 'cad'.
            """
            res = await claude_code_adapter.run_claude(project_id, job_id, prompt, ch)
            if res.get("error") == "cancelled" or job_id in claude_code_adapter._cancelled:
                return "cancel", res
            return ("ok" if res["ok"] else "fail"), res

        async def _do_cancel() -> None:
            if job:
                job.status = "CANCELLED"; job.completed_at = _now(); job_service.save_job(job)
            await ch.publish(SOURCE_SYSTEM, "job.cancelled", stage="claude",
                             message="Generation cancelled")

        code: str | None = None

        if hierarchical:
            # ---- 3-4. Generate + validate each component independently ----
            if job:
                job.stage = "COMPONENT_GENERATION"; job_service.save_job(job)
            results: list[dict] = []
            for comp in manifest["components"]:
                await ch.publish(SOURCE_QWEN, "planner.delta", stage="components",
                                 message=f"Component: {comp['name']} ({comp['role']})")
                c_reason: str | None = None
                c_repair = 0
                while True:
                    cprompt = component_validator.component_prompt(design_spec, comp)
                    if c_reason:
                        cprompt += component_validator.repair_prompt(comp, c_reason)
                    status, res = await _claude_call(cprompt)
                    if status == "cancel":
                        await _do_cancel(); return
                    if status == "fail":
                        fc = res.get("failure_class")
                        if fc == "quota":
                            await fail("COMPONENT_GENERATION",
                                       res.get("error") or "quota limit reached",
                                       status="FAILED_QUOTA")
                            return
                        if fc == "turns":
                            await fail("COMPONENT_GENERATION",
                                       res.get("error") or "max turns reached",
                                       status="FAILED_TURNS")
                            return
                        # fc == "cad" or None — enter/continue repair loop
                        c_reason = res.get("error") or "Claude failed"
                    else:
                        cpath = claude_code_adapter.safe_workspace_path(
                            claude_code_adapter.workspace_dir(project_id), comp["source"])
                        if cpath is None or not cpath.is_file():
                            c_reason = "component source not written"
                        else:
                            await ch.publish(SOURCE_WORKER, "cad.execution.started",
                                             stage="cad_execution",
                                             message=f"Validating component {comp['name']}")
                            ex = await asyncio.to_thread(
                                component_validator.run_component, project_id, comp, cpath.read_text())
                            v = component_validator.validate_component(comp, ex)
                            if v["valid"]:
                                await ch.publish(SOURCE_WORKER, "cad.execution.completed",
                                                 stage="cad_execution",
                                                 message=f"Component {comp['name']} valid {v['facts']}")
                                comp["status"] = "valid"; results.append(v); break
                            c_reason = v["reason"]
                    c_repair += 1
                    await ch.publish(SOURCE_WORKER, "cad.execution.log", stage="validation",
                                     message=f"Component {comp['name']} repair "
                                             f"{c_repair}/{config.CLAUDE_CODE_MAX_REPAIRS}: {str(c_reason)[:120]}")
                    if c_repair > config.CLAUDE_CODE_MAX_REPAIRS:
                        comp["status"] = "invalid"
                        results.append({"name": comp["name"], "step": comp["step"],
                                        "valid": False, "reason": c_reason, "facts": None})
                        # Attempt all remaining components before failing; break inner loop only.
                        break

            report = component_validator.write_report(project_id, results)
            await ch.publish(SOURCE_WORKER, "cad.execution.log", stage="validation",
                             message=f"Components validated: {report['passed']}/{report['total']}")
            if report["passed"] < report["total"]:
                bad = [r["name"] for r in results if not r["valid"]]
                await fail("COMPONENT_VALIDATION", f"components failed validation: {bad}",
                           status="FAILED_CAD")
                return

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

        else:
            # ---- Single-shot path (simple parts) — unchanged behavior ----
            repair_error: str | None = None
            attempt = 0
            quality_attempt = 0
            while True:
                prompt = claude_prompt
                if repair_error:
                    prompt = (claude_prompt
                              + "\n\n--- REWRITE REQUIRED (repair) ---\n" + repair_error
                              + "\nChange only the smallest responsible source section.\n")
                status, res = await _claude_call(prompt)
                if status == "cancel":
                    await _do_cancel(); return
                if status == "fail":
                    await fail("CLAUDE_CODE_GENERATION", res.get("error") or "Claude Code failed")
                    return
                code, err = _read_generate_py(project_id)
                if code is None:
                    await fail("CLAUDE_CODE_GENERATION", err or "missing generate.py")
                    return
                await ch.publish(SOURCE_WORKER, "cad.execution.started", stage="cad_execution",
                                 message="Executing build123d source -> STEP/STL/GLB")
                if job:
                    job.stage = "CAD_EXECUTION"; job_service.save_job(job)
                cad = await _run_cad(project_id, code)
                if cad["ok"]:
                    await ch.publish(SOURCE_WORKER, "cad.execution.completed", stage="cad_execution",
                                     message="CAD execution succeeded")
                    q = _quality_gate(project_id, code, spec)
                    if q["flags"]:
                        await ch.publish(SOURCE_WORKER, "cad.execution.log", stage="validation",
                                         message=f"Quality flags: {', '.join(q['flags'])}")
                    if q["primitive_box_output"] and quality_attempt < 1:
                        quality_attempt += 1
                        repair_error = (
                            "The result is a primitive box and does not satisfy the requested "
                            f"{spec.get('object_kind', 'object')}. Rewrite with the required features.")
                        continue
                    break
                repair_error = cad.get("error", "CAD execution failed")
                await ch.publish(SOURCE_WORKER, "cad.execution.log", stage="cad_execution",
                                 message=f"CAD failed: {repair_error[:200]}")
                attempt += 1
                if attempt > config.CLAUDE_CODE_MAX_REPAIRS:
                    await fail("CAD_EXECUTION", f"failed after {attempt - 1} repair(s): {repair_error}")
                    return

        # ---- 9. artifacts + report ----
        if job:
            job.stage = "ARTIFACTS"; job_service.save_job(job)
        try:
            await asyncio.to_thread(report_service.generate_summary_report, project_id)
        except Exception:  # noqa: BLE001 - report is advisory
            pass
        listing = artifact_service.list_artifacts(project_id)
        for art in (listing.artifacts if listing else []):
            await ch.publish(SOURCE_ARTIFACT, "artifact.created", stage="artifacts",
                             message=art.name,
                             data={"path": art.relative_path, "category": art.category,
                                   "download_url": art.download_url, "viewer_url": art.viewer_url})

        if job:
            job.status = "COMPLETED"; job.stage = "COMPLETED"; job.completed_at = _now()
            job_service.save_job(job)
        await ch.publish(SOURCE_SYSTEM, "job.completed", stage="complete",
                         message="Generation completed")
    except Exception as exc:  # noqa: BLE001 - any unexpected failure
        await fail("ERROR", f"{type(exc).__name__}: {exc}")


def start(project_id: str, job_id: str) -> None:
    """Fire-and-forget the async pipeline on the running event loop."""
    asyncio.create_task(run(project_id, job_id))
