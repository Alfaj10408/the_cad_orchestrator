"""/v1 production API facade. Wraps existing pipeline services."""
from __future__ import annotations
import json, uuid
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Header, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from app.core import config, paths
from app.services import job_service, artifact_service, claude_code_adapter
from app.v1 import auth, db
from app.v1.models import JobCreate, JobView
from app.api.events import _gen

router = APIRouter(prefix="/v1", tags=["v1"])
_ALLOWED_MODES = {"qwen_claude_code", "deterministic"}

def _owned_row(conn, job_id, user_id):
    row = db.get_job_row(conn, job_id)
    if row is None or row["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="job not found")
    return row

@router.post("/jobs", status_code=201)
def create_job(body: JobCreate, request: Request, user_id: str = Depends(auth.require_user),
               conn=Depends(db.get_conn)):
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
    db.insert_job(conn, job.job_id, user_id, pid, status="pending")
    try:
        request.app.state.queue.enqueue(job.job_id)
    except RuntimeError:
        db.update_job(conn, job.job_id, status="failed", failure_class="internal")
        raise HTTPException(status_code=429, detail="queue full")
    return {"job_id": job.job_id, "status": "pending",
            "queue_pos": db.pending_position(conn, job.job_id)}

@router.get("/me")
def whoami(user_id: str = Depends(auth.require_user), conn=Depends(db.get_conn)):
    u = db.get_user(conn, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="user not found")
    return {"user_id": user_id, "name": u["name"], "is_admin": bool(u["is_admin"])}

@router.get("/jobs/{job_id}", response_model=JobView)
def get_job(job_id: str, user_id: str = Depends(auth.require_user),
            conn=Depends(db.get_conn)):
    r = _owned_row(conn, job_id, user_id)
    return JobView(job_id=r["job_id"], status=r["status"], stage=r["stage"],
                   failure_class=r["failure_class"],
                   queue_pos=db.pending_position(conn, job_id),
                   created_at=r["created_at"], started_at=r["started_at"],
                   completed_at=r["completed_at"])

@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, user_id: str = Depends(auth.require_user),
               conn=Depends(db.get_conn)):
    r = _owned_row(conn, job_id, user_id)
    killed = claude_code_adapter.cancel(job_id)
    if r["status"] in ("pending", "running"):
        db.update_job(conn, job_id, status="cancelled")
    return {"job_id": job_id, "requested": True, "killed_process": killed}

@router.get("/jobs/{job_id}/artifacts")
def list_artifacts(job_id: str, user_id: str = Depends(auth.require_user),
                   conn=Depends(db.get_conn)):
    r = _owned_row(conn, job_id, user_id)
    listing = artifact_service.list_artifacts(r["project_id"])
    arts = listing.artifacts if listing else []
    return {"artifacts": [{"name": a.name, "category": a.category,
                           "relative_path": a.relative_path} for a in arts]}

@router.get("/jobs/{job_id}/artifacts/{rel:path}")
def download_artifact(job_id: str, rel: str, user_id: str = Depends(auth.require_user),
                      conn=Depends(db.get_conn)):
    r = _owned_row(conn, job_id, user_id)
    root = paths.project_dir(r["project_id"]).resolve()
    rel_path = Path(rel)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        raise HTTPException(status_code=404, detail="artifact not found")
    target = (root / rel_path).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(str(target), filename=target.name)

@router.get("/jobs/{job_id}/events")
async def stream_events(job_id: str, request: Request,
                        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
                        user_id: str = Depends(auth.require_user),
                        conn=Depends(db.get_conn)) -> StreamingResponse:
    r = _owned_row(conn, job_id, user_id)
    try: last_id = int(last_event_id) if last_event_id else 0
    except ValueError: last_id = 0
    return StreamingResponse(_gen(r["project_id"], job_id, last_id, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"})

@router.get("/healthz")
def healthz():
    return {"ok": True}

@router.get("/readyz")
def readyz(request: Request):
    checks = {}
    try:
        c = db.connect(); c.execute("SELECT 1"); c.close()
        checks["db"] = True
    except Exception:
        checks["db"] = False
    try:
        checks["claude_code"] = bool(claude_code_adapter.health().get("authenticated"))
    except Exception:
        checks["claude_code"] = False
    q = request.app.state.queue
    checks["worker"] = bool(getattr(q, "alive", lambda: True)())
    ready = all(checks.values())
    return {"ready": ready, "checks": checks}

class _KeyReq(BaseModel):
    user_name: str

@router.post("/admin/keys", status_code=201)
def admin_mint_key(body: _KeyReq, _: bool = Depends(auth.require_admin),
                   conn=Depends(db.get_conn)):
    key, prefix, kid, uid = auth.mint_key(conn, body.user_name)
    return {"key": key, "key_prefix": prefix, "key_id": kid, "user_id": uid}

@router.delete("/admin/keys/{key_id}")
def admin_revoke_key(key_id: str, _: bool = Depends(auth.require_admin),
                     conn=Depends(db.get_conn)):
    db.revoke_key(conn, key_id)
    return {"revoked": key_id}
