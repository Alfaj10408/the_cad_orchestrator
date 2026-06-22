"""/v1 production API facade. Wraps existing pipeline services."""
from __future__ import annotations
import json, uuid, time, shutil
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Header, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from app.core import config, paths
from app.services import job_service, artifact_service, claude_code_adapter
from app.v1 import auth, db
from app.v1.models import JobCreate, JobView
from app.api.events import _gen
from app.ai.llm import client as orch_client, config as orch_cfg

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
    if config.API_QUOTA_ENABLED:
        u = db.get_user(conn, user_id)
        if not (u and u["is_admin"]):
            daily_limit, max_in_flight = db.get_quota(conn, user_id)
            if db.count_in_flight(conn, user_id) >= max_in_flight:
                raise HTTPException(status_code=429, detail={
                    "detail": "max in-flight jobs reached", "scope": "in_flight",
                    "limit": max_in_flight, "used": db.count_in_flight(conn, user_id)})
            since = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0).isoformat()
            used = db.count_created_since(conn, user_id, since)
            if used >= daily_limit:
                raise HTTPException(status_code=429, detail={
                    "detail": "daily job limit reached", "scope": "daily",
                    "limit": daily_limit, "used": used})
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

_readyz_cache: dict = {}

def _cached(key, fn):
    now = time.monotonic()
    hit = _readyz_cache.get(key)
    if hit is not None and (now - hit[0]) < config.API_READYZ_CACHE_S:
        return hit[1]
    val = fn()
    _readyz_cache[key] = (now, val)
    return val

def _check_db() -> bool:
    try:
        c = db.connect(); c.execute("SELECT 1"); c.close(); return True
    except Exception:
        return False

def _check_storage() -> bool:
    try:
        d = config.PROJECTS_ROOT
        d.mkdir(parents=True, exist_ok=True)
        p = d / ".readyz_write_test"
        p.write_text("ok"); p.unlink()
        return True
    except Exception:
        return False

def _check_disk() -> bool:
    try:
        free = shutil.disk_usage(str(config.STORAGE_ROOT)).free
        return free >= config.API_MIN_DISK_MB * 1024 * 1024
    except Exception:
        return False

def _check_claude() -> bool:
    try:
        h = claude_code_adapter.health()
        return bool(h.get("installed") and h.get("authenticated"))
    except Exception:
        return False

def _check_orchestrator() -> bool:
    try:
        return bool(orch_client.health().get("ok"))
    except Exception:
        return False

@router.get("/readyz")
def readyz(request: Request):
    checks: dict = {}
    checks["db"] = _check_db()
    q = request.app.state.queue
    checks["queue"] = bool(getattr(q, "alive", lambda: True)())
    checks["storage"] = _check_storage()
    checks["disk"] = _check_disk()
    checks["orchestrator"] = (
        _cached("orchestrator", _check_orchestrator)
        if orch_cfg.ORCHESTRATOR_ENABLED else "skipped")
    checks["claude_code"] = (
        _cached("claude_code", _check_claude)
        if config.CLAUDE_CODE_ENABLED else "skipped")
    ready = all(v is True for v in checks.values() if isinstance(v, bool))
    body = {"ready": ready, "checks": checks,
            "timestamp": datetime.now(timezone.utc).isoformat()}
    return JSONResponse(status_code=200 if ready else 503, content=body)

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
