"""Worker trigger endpoint (local MVP use only).

Runs one synchronous pass of the generation worker — same logic as
scripts/run_worker_once.sh. No queue backend.
"""
from fastapi import APIRouter

from app.workers import runner

router = APIRouter(prefix="/workers", tags=["workers"])


@router.post("/run-once")
def run_once() -> dict:
    job_ids = runner.run_once()
    return {"processed_count": len(job_ids), "job_ids": job_ids}
