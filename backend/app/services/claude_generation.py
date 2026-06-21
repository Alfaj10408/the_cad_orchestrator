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
    report_service,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_brief(project_id: str) -> dict:
    p = paths.project_dir(project_id) / "brief.json"
    return json.loads(p.read_text()) if p.exists() else {}


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
        await ch.publish(
            SOURCE_QWEN, "planner.delta", stage="planning",
            message=(f"Decomposed into {manifest['component_count']} components: "
                     + ", ".join(c["name"] for c in manifest["components"])),
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
        try:
            graph = assembly_graph.build_graph(manifest, design_spec)
            placement_rules.resolve(graph, design_spec)
            assembly_graph.write_graph(project_id, graph)
            assembly_composer.write_source(project_id, graph)
        except ValueError as exc:
            await fail("ASSEMBLY_GENERATION", str(exc), status="FAILED_CAD")
            return
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
