"""/v1 production API facade. Wraps existing pipeline services."""
from __future__ import annotations
import json, uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from app.core import config, paths
from app.services import job_service, artifact_service, claude_code_adapter, event_service
from app.v1 import auth, db
from app.v1.models import JobCreate, JobView

router = APIRouter(prefix="/v1", tags=["v1"])
_ALLOWED_MODES = {"qwen_claude_code", "deterministic"}

def _conn(request: Request):
    return request.app.state.db

def _owned_row(request, job_id, user_id):
    row = db.get_job_row(_conn(request), job_id)
    if row is None or row["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="job not found")
    return row

@router.post("/jobs", status_code=201)
def create_job(body: JobCreate, request: Request, user_id: str = Depends(auth.require_user)):
    if body.mode not in _ALLOWED_MODES:
        raise HTTPException(status_code=422, detail="invalid mode")
    if len(body.prompt) > config.CLAUDE_CODE_MAX_PROMPT_CHARS:
        raise HTTPException(status_code=422, detail="prompt too long")
    pid = uuid.uuid4().hex
    paths.ensure_project_skeleton(pid)
    (paths.project_dir(pid) / "brief.json").write_text(json.dumps({
        "project_id": pid, "prompt": body.prompt, "intent": "concept_cad",
        "parameters": {"dimensions": body.dimensions or "", "units": "mm",
                       "material": body.material},
        "user_answers": {"dimensions": body.dimensions or ""},
        "ready_to_generate": True, "generation_mode": body.mode}))
    job = job_service.create_job_full(pid, "generation", "CREATED")
    db.insert_job(_conn(request), job.job_id, user_id, pid, status="pending")
    try:
        pos = request.app.state.queue.enqueue(job.job_id)
    except RuntimeError:
        db.update_job(_conn(request), job.job_id, status="failed", failure_class="internal")
        raise HTTPException(status_code=429, detail="queue full")
    db.update_job(_conn(request), job.job_id, queue_pos=pos)
    return {"job_id": job.job_id, "status": "pending", "queue_pos": pos}

@router.get("/me")
def whoami(request: Request, user_id: str = Depends(auth.require_user)):
    u = db.get_user(_conn(request), user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="user not found")
    return {"user_id": user_id, "name": u["name"], "is_admin": bool(u["is_admin"])}

@router.get("/jobs/{job_id}", response_model=JobView)
def get_job(job_id: str, request: Request, user_id: str = Depends(auth.require_user)):
    r = _owned_row(request, job_id, user_id)
    return JobView(job_id=r["job_id"], status=r["status"], stage=r["stage"],
                   failure_class=r["failure_class"], queue_pos=r["queue_pos"],
                   created_at=r["created_at"], started_at=r["started_at"],
                   completed_at=r["completed_at"])

@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request, user_id: str = Depends(auth.require_user)):
    r = _owned_row(request, job_id, user_id)
    killed = claude_code_adapter.cancel(job_id)
    if r["status"] in ("pending", "running"):
        db.update_job(_conn(request), job_id, status="cancelled")
    return {"job_id": job_id, "requested": True, "killed_process": killed}

@router.get("/jobs/{job_id}/artifacts")
def list_artifacts(job_id: str, request: Request, user_id: str = Depends(auth.require_user)):
    r = _owned_row(request, job_id, user_id)
    listing = artifact_service.list_artifacts(r["project_id"])
    arts = listing.artifacts if listing else []
    return {"artifacts": [{"name": a.name, "category": a.category,
                           "relative_path": a.relative_path} for a in arts]}

@router.get("/jobs/{job_id}/artifacts/{name}")
def download_artifact(job_id: str, name: str, request: Request,
                      user_id: str = Depends(auth.require_user)):
    r = _owned_row(request, job_id, user_id)
    root = paths.project_dir(r["project_id"]).resolve()
    target = (root / "cad" / name).resolve()
    if not str(target).startswith(str(root)) or not target.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(str(target), filename=name)
