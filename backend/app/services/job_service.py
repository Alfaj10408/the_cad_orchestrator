"""Job service: JSON file storage."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from app.core import paths
from app.schemas.job import Job, JobCreate


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(project_id: str, payload: JobCreate) -> Job:
    paths.jobs_dir(project_id).mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex
    job = Job(
        job_id=job_id,
        project_id=project_id,
        kind=payload.kind or "text_to_cad",
        status="CREATED",
        created_at=_now(),
    )
    paths.job_path(project_id, job_id).write_text(job.model_dump_json(indent=2))
    return job


def create_job_full(
    project_id: str, kind: str, status: str, stage: str | None = None
) -> Job:
    """Create a job with explicit kind/status/stage."""
    paths.jobs_dir(project_id).mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex
    job = Job(
        job_id=job_id,
        project_id=project_id,
        kind=kind,
        status=status,
        stage=stage,
        created_at=_now(),
    )
    paths.job_path(project_id, job_id).write_text(job.model_dump_json(indent=2))
    return job


def list_jobs(project_id: str, kind: str | None = None) -> list[Job]:
    """All jobs for a project, newest first; optionally filtered by kind."""
    jdir = paths.jobs_dir(project_id)
    if not jdir.exists():
        return []
    jobs: list[Job] = []
    for jp in jdir.glob("*.json"):
        job = Job(**json.loads(jp.read_text()))
        if kind is None or job.kind == kind:
            jobs.append(job)
    jobs.sort(key=lambda j: j.created_at, reverse=True)
    return jobs


def latest_job(project_id: str, kind: str) -> Job | None:
    jobs = list_jobs(project_id, kind)
    return jobs[0] if jobs else None


def save_job(job: Job) -> Job:
    """Persist a (mutated) job back to its file."""
    paths.jobs_dir(job.project_id).mkdir(parents=True, exist_ok=True)
    paths.job_path(job.project_id, job.job_id).write_text(job.model_dump_json(indent=2))
    return job


def get_job(job_id: str) -> Job | None:
    """Find a job by id across all projects."""
    from app.core.config import PROJECTS_ROOT

    if not PROJECTS_ROOT.exists():
        return None
    for proj in PROJECTS_ROOT.iterdir():
        jp = proj / "jobs" / f"{job_id}.json"
        if jp.exists():
            return Job(**json.loads(jp.read_text()))
    return None
