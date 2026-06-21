"""Prompt analysis + clarification endpoints (rule-based, no LLM)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.ai import brief_generator, clarifier, orchestrator, planner
from app.ai.llm import config as orch_config
from app.ai.llm.client import OrchestratorError
from app.core import paths
from app.schemas.clarification import ClarificationSubmit
from app.schemas.generation import (
    AnalyzeRequest,
    AnalyzeResponse,
    Brief,
    GenerateRequest,
    GenerateResponse,
    GenerationStatus,
    Intent,
    PromptAnalysis,
)
from app.services import claude_generation, job_service, project_service


def _load_json(path) -> dict | None:
    if path.exists():
        return json.loads(path.read_text())
    return None

router = APIRouter(prefix="/projects", tags=["analysis"])


def _persist_analysis(
    project_id, prompt, intent, confidence, signals, missing, questions, brief, *, source
) -> AnalyzeResponse:
    """Write brief.json + prompt_analysis.json and build the response."""
    root = paths.project_dir(project_id)
    (root / "brief.json").write_text(brief.model_dump_json(indent=2))

    analysis = PromptAnalysis(
        prompt=prompt,
        intent=intent,
        confidence=confidence,
        signals=signals,
        missing=missing,
    )
    reports = root / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "prompt_analysis.json").write_text(analysis.model_dump_json(indent=2))
    (reports / "analysis_source.txt").write_text(source)

    return AnalyzeResponse(
        project_id=project_id,
        intent=intent,
        confidence=confidence,
        ready_to_generate=brief.ready_to_generate,
        questions=questions,
        brief=brief,
    )


def _rule_based_analysis(project_id: str, prompt: str) -> AnalyzeResponse:
    intent, confidence, signals = planner.classify(prompt)
    params = planner.extract_parameters(prompt)
    missing = clarifier.detect_missing(prompt, intent, params)
    questions = clarifier.build_questions(missing)

    brief = brief_generator.build_brief(project_id, prompt, intent, params)
    brief.ready_to_generate = len(missing) == 0
    return _persist_analysis(
        project_id, prompt, intent, confidence, signals, missing, questions, brief,
        source="rule_based",
    )


def _orchestrated_analysis(project_id: str, prompt: str) -> AnalyzeResponse:
    a = orchestrator.analyze(prompt)
    brief = Brief(
        project_id=project_id,
        prompt=prompt,
        intent=a.intent,
        summary=a.summary,
        parameters=a.parameters,
        assumptions=a.assumptions,
        ready_to_generate=a.ready_to_generate,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    return _persist_analysis(
        project_id, prompt, a.intent, a.confidence, a.signals, a.missing, a.questions,
        brief, source="orchestrator",
    )


def run_analysis(project_id: str, prompt: str) -> AnalyzeResponse:
    """Analyze prompt -> brief.json + analysis report.

    Uses the Qwen orchestrator when enabled and reachable; otherwise (or on
    any orchestrator error) falls back to the deterministic rule-based path.
    """
    if orch_config.ORCHESTRATOR_ENABLED:
        try:
            return _orchestrated_analysis(project_id, prompt)
        except OrchestratorError as exc:
            reports = paths.project_dir(project_id) / "reports"
            reports.mkdir(parents=True, exist_ok=True)
            (reports / "orchestrator_error.txt").write_text(str(exc))
    return _rule_based_analysis(project_id, prompt)


@router.post("/{project_id}/analyze", response_model=AnalyzeResponse)
def analyze(project_id: str, payload: AnalyzeRequest) -> AnalyzeResponse:
    if project_service.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    if not payload.prompt or not payload.prompt.strip():
        raise HTTPException(status_code=422, detail="prompt is required")
    return run_analysis(project_id, payload.prompt)


@router.post("/{project_id}/clarifications", response_model=AnalyzeResponse)
def submit_clarifications(project_id: str, payload: ClarificationSubmit) -> AnalyzeResponse:
    if project_service.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")

    answers = payload.answers or {}
    root = paths.project_dir(project_id)
    reports = root / "reports"
    reports.mkdir(exist_ok=True)

    # Save raw answers.
    (reports / "clarification_answers.json").write_text(
        json.dumps(
            {"answers": answers, "submitted_at": datetime.now(timezone.utc).isoformat()},
            indent=2,
        )
    )

    # Load prior brief / analysis to recover prompt + base state.
    brief_doc = _load_json(root / "brief.json")
    analysis_doc = _load_json(reports / "prompt_analysis.json")

    prompt = ""
    if brief_doc:
        prompt = brief_doc.get("prompt", "")
    if not prompt and analysis_doc:
        prompt = analysis_doc.get("prompt", "")

    # Base intent/params: prefer prompt classification, then stored values.
    intent, confidence, signals = planner.classify(prompt)
    params = planner.extract_parameters(prompt)
    if brief_doc and brief_doc.get("parameters"):
        params = {**params, **brief_doc["parameters"]}

    # Fold in the user's answers and re-run deterministic analysis.
    intent, params = clarifier.apply_answers(intent, params, answers)
    missing = clarifier.detect_missing(prompt, intent, params)
    questions = clarifier.build_questions(missing)
    ready = len(missing) == 0

    brief = brief_generator.build_brief(project_id, prompt, intent, params)
    brief.user_answers = answers
    brief.ready_to_generate = ready
    (root / "brief.json").write_text(brief.model_dump_json(indent=2))

    analysis = PromptAnalysis(
        prompt=prompt,
        intent=intent,
        confidence=confidence,
        signals=signals,
        missing=missing,
    )
    (reports / "prompt_analysis.json").write_text(analysis.model_dump_json(indent=2))

    return AnalyzeResponse(
        project_id=project_id,
        intent=intent,
        confidence=confidence,
        ready_to_generate=ready,
        questions=questions,
        brief=brief,
    )


# deterministic | qwen_claude_code | anthropic_api  ("llm" kept as legacy alias)
_ALLOWED_MODES = {"deterministic", "qwen_claude_code", "anthropic_api"}


@router.post("/{project_id}/generate", response_model=GenerateResponse)
async def generate(
    project_id: str, payload: GenerateRequest | None = None
) -> GenerateResponse:
    if project_service.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")

    mode = (payload.generation_mode if payload else "deterministic").lower()
    if mode == "llm":
        mode = "anthropic_api"
    if mode not in _ALLOWED_MODES:
        raise HTTPException(status_code=422, detail="invalid generation_mode")

    root = paths.project_dir(project_id)
    brief_doc = _load_json(root / "brief.json")
    if brief_doc is None:
        raise HTTPException(status_code=409, detail="project is not analyzed yet")
    if not brief_doc.get("ready_to_generate"):
        raise HTTPException(status_code=409, detail="project still needs clarification")

    # Persist the chosen mode into brief.json so the worker can read it.
    brief_doc["generation_mode"] = mode
    (root / "brief.json").write_text(json.dumps(brief_doc, indent=2))

    # qwen_claude_code streams in real time -> start RUNNING job + async pipeline.
    # Other modes use the queue + /workers/run-once path (unchanged).
    if mode == "qwen_claude_code":
        job = job_service.create_job_full(
            project_id, kind="generation", status="RUNNING", stage="PLANNING"
        )
        claude_generation.start(project_id, job.job_id)
        message = "qwen_claude_code generation started (stream events)"
    else:
        job = job_service.create_job_full(
            project_id, kind="generation", status="QUEUED", stage="GENERATING_PENDING"
        )
        message = "generation job queued"

    reports = root / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "generation_request.json").write_text(
        json.dumps(
            {
                "project_id": project_id,
                "job_id": job.job_id,
                "generation_mode": mode,
                "brief_summary": brief_doc.get("summary", ""),
                "created_at": job.created_at,
            },
            indent=2,
        )
    )

    return GenerateResponse(
        project_id=project_id,
        job_id=job.job_id,
        status=job.status,
        stage=job.stage or "",
        message=message,
    )


@router.get("/{project_id}/generation-status", response_model=GenerationStatus)
def generation_status(project_id: str) -> GenerationStatus:
    if project_service.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")

    job = job_service.latest_job(project_id, kind="generation")
    if job is None:
        return GenerationStatus(project_id=project_id, status="not_started")
    return GenerationStatus(
        project_id=project_id,
        status=job.status,
        stage=job.stage,
        job_id=job.job_id,
        created_at=job.created_at,
    )
