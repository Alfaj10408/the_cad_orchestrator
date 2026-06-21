"""MVP v1 generation task orchestration.

Deterministic mode: CAD_SOURCE_GENERATION -> STEP_EXPORT -> ...
LLM mode: ORCHESTRATION -> CAD_SOURCE_GENERATION -> STEP_EXPORT ->
          STL_GLB_EXPORT -> CAD_INSPECTION -> SNAPSHOT_GENERATION -> COMPLETED
The ORCHESTRATION stage builds the worker prompt (orchestrator or template).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.ai import cad_agent, repair_agent, report_agent
from app.core import paths
from app.schemas.job import Job
from app.services import (
    artifact_service,
    cad_runner,
    job_service,
    llm_cad_generator,
    report_service,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(project_id: str, line: str) -> None:
    reports = paths.project_dir(project_id) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    with (reports / "generation_worker.log").open("a") as fh:
        fh.write(f"{_now()} {line}\n")


def _load_brief(project_id: str) -> dict:
    brief_path = paths.project_dir(project_id) / "brief.json"
    if not brief_path.exists():
        raise FileNotFoundError("brief.json not found")
    return json.loads(brief_path.read_text())


def _fail(job: Job, stage: str, detail: str) -> Job:
    job.status = "FAILED"
    job.stage = f"{stage}_FAILED"
    job.completed_at = _now()
    job_service.save_job(job)
    reports = paths.project_dir(job.project_id) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "generation_error.txt").write_text(f"[{stage}] {detail}")
    _log(job.project_id, f"job {job.job_id} {job.stage}: {detail[:200]}")
    return job


def _enter(job: Job, stage: str) -> None:
    job.stage = stage
    job_service.save_job(job)
    _log(job.project_id, f"job {job.job_id} {stage}")


def _write_llm_error(project_id: str, detail: str) -> None:
    reports = paths.project_dir(project_id) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "llm_generation_error.txt").write_text(detail)


def _llm_source_and_export(job: Job, brief: dict):
    """LLM source generation + STEP export with one repair attempt.

    Returns (src_meta, step) on success, or None after recording a failure.
    Never silently falls back to the deterministic template.
    """
    pid = job.project_id

    if not llm_cad_generator.is_configured():
        return _fail(
            job,
            "CAD_SOURCE_GENERATION",
            "LLM mode requested but ANTHROPIC_API_KEY is not configured",
        ) and None

    # ORCHESTRATION: build the structured worker prompt for the Claude worker.
    _enter(job, "ORCHESTRATION")
    worker_prompt, prompt_source = cad_agent.build_worker_prompt(brief)
    reports = paths.project_dir(pid) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "worker_prompt.txt").write_text(worker_prompt)
    _log(pid, f"job {job.job_id} worker prompt built ({prompt_source})")

    # First attempt.
    _enter(job, "CAD_SOURCE_GENERATION")
    try:
        code = llm_cad_generator.generate_source(brief, user_prompt=worker_prompt)
    except Exception as exc:  # noqa: BLE001 - LLM/safety failure
        _write_llm_error(pid, f"generation failed: {exc}")
        _fail(job, "CAD_SOURCE_GENERATION", str(exc))
        return None

    src_meta = cad_runner.generate_source_from_llm(pid, code)
    _enter(job, "STEP_EXPORT")
    step = cad_runner.export_step(pid)
    if step["ok"]:
        return src_meta, step

    # CAD export failed — bounded repair loop driven by the repair agent.
    max_repairs = repair_agent.DEFAULT_MAX_REPAIRS
    attempt = 0
    while not step["ok"]:
        err = step.get("stderr") or "STEP export failed"
        _write_llm_error(pid, f"step export failed (attempt {attempt}):\n{err}")

        decision = repair_agent.decide(brief, err, attempt, max_repairs)
        _log(pid, f"job {job.job_id} repair decision: {decision.action} ({decision.reason})")
        if decision.action != "repair":
            _fail(job, "STEP_EXPORT", f"repair aborted: {decision.reason}; last error: {err}")
            return None

        attempt += 1
        repair_prompt = worker_prompt
        if decision.hint:
            repair_prompt = worker_prompt + "\n\nOrchestrator hint:\n" + decision.hint
        try:
            code = llm_cad_generator.repair_source(
                brief, code, err, user_prompt=repair_prompt
            )
        except Exception as exc:  # noqa: BLE001
            _write_llm_error(pid, f"repair failed: {exc}")
            _fail(job, "STEP_EXPORT", f"LLM repair failed: {exc}")
            return None

        src_meta = cad_runner.generate_source_from_llm(pid, code)
        step = cad_runner.export_step(pid)

    return src_meta, step


def process_generation_job(job: Job) -> Job:
    """Run the deterministic CAD pipeline for one job."""
    pid = job.project_id

    job.status = "RUNNING"
    job.started_at = _now()
    _enter(job, "CAD_SOURCE_GENERATION")

    try:
        brief = _load_brief(pid)
    except Exception as exc:  # noqa: BLE001
        return _fail(job, "CAD_SOURCE_GENERATION", str(exc))

    # CAD source generation + STEP export, branching on generation_mode.
    mode = (brief.get("generation_mode") or "deterministic").lower()
    if mode in ("anthropic_api", "llm"):
        result = _llm_source_and_export(job, brief)
        if result is None:
            return job  # _fail already recorded the failure
        src_meta, step = result
    else:
        src_meta = cad_runner.generate_source_from_template(pid, brief)
        _enter(job, "STEP_EXPORT")
        step = cad_runner.export_step(pid)
        if not step["ok"]:
            return _fail(job, "STEP_EXPORT", step.get("stderr") or "STEP export failed")

    # STL_GLB_EXPORT.
    _enter(job, "STL_GLB_EXPORT")
    meshes = cad_runner.export_meshes(pid)
    if not meshes["ok"]:
        return _fail(job, "STL_GLB_EXPORT", meshes.get("stderr") or "mesh export failed")

    # CAD_INSPECTION.
    _enter(job, "CAD_INSPECTION")
    insp = cad_runner.inspect_step(pid)
    if not insp["ok"]:
        return _fail(job, "CAD_INSPECTION", insp.get("stderr") or "inspect failed")

    # Advisory: orchestrator/report agent assembles findings (non-fatal).
    findings = report_agent.inspect(pid, step, meshes, insp)
    _log(pid, f"job {job.job_id} findings issues: {findings.get('issues')}")

    # SNAPSHOT_GENERATION.
    _enter(job, "SNAPSHOT_GENERATION")
    snap = cad_runner.generate_snapshot(pid)
    if not snap["ok"]:
        return _fail(job, "SNAPSHOT_GENERATION", snap.get("stderr") or "snapshot failed")

    # REPORT_GENERATION.
    _enter(job, "REPORT_GENERATION")
    try:
        report_rel = report_service.generate_summary_report(pid)
    except Exception as exc:  # noqa: BLE001
        return _fail(job, "REPORT_GENERATION", str(exc))

    # ARTIFACT_URLS_AND_VIEWER.
    _enter(job, "ARTIFACT_URLS_AND_VIEWER")
    try:
        artifact_urls, viewer_urls = artifact_service.url_maps(pid)
    except Exception as exc:  # noqa: BLE001
        return _fail(job, "ARTIFACT_URLS_AND_VIEWER", str(exc))

    # COMPLETED.
    job.status = "COMPLETED"
    job.stage = "COMPLETED"
    job.completed_at = _now()
    job_service.save_job(job)
    _log(pid, f"job {job.job_id} COMPLETED")

    reports = paths.project_dir(pid) / "reports"
    (reports / "generation_result.json").write_text(
        json.dumps(
            {
                "message": "MVP v1 CAD generation completed",
                "generation_mode": src_meta.get("mode", "deterministic"),
                "dimensions": src_meta.get("dimensions"),
                "is_bracket": src_meta.get("is_bracket", False),
                "artifacts": {
                    "source": step["source"],
                    "step": step["step"],
                    "stl": meshes["stl"],
                    "glb": meshes["glb"],
                    "snapshot": snap["snapshot"],
                    "inspection": insp["report"],
                    "summary": report_rel,
                },
                "artifact_urls": artifact_urls,
                "viewer_urls": viewer_urls,
                "next_real_stage": "MVP1_FRONTEND_OR_LLM_CAD",
            },
            indent=2,
        )
    )
    return job
