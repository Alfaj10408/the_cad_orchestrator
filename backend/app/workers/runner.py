"""Single-pass generation worker (no loop, no queue backend)."""
from __future__ import annotations

from app.core.config import PROJECTS_ROOT
from app.schemas.job import Job
from app.services import job_service
from app.workers import tasks


def run_once() -> list[str]:
    """Process every QUEUED generation job once. Returns processed job ids."""
    processed: list[str] = []
    if not PROJECTS_ROOT.exists():
        return processed

    for proj in sorted(PROJECTS_ROOT.iterdir()):
        if not proj.is_dir():
            continue
        for job in job_service.list_jobs(proj.name, kind="generation"):
            if job.status == "QUEUED":
                tasks.process_generation_job(job)
                processed.append(job.job_id)
    return processed


if __name__ == "__main__":
    done = run_once()
    print(f"processed {len(done)} generation job(s): {done}")
