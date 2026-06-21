"""Job endpoints."""
from fastapi import APIRouter, HTTPException

from app.schemas.job import Job
from app.services import job_service

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=Job)
def get_job(job_id: str) -> Job:
    job = job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job
