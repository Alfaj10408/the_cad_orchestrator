"""Project + nested job/artifact endpoints."""
import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.core import paths
from app.schemas.artifact import ArtifactList, ViewerResponse
from app.schemas.job import Job, JobCreate
from app.schemas.project import Project, ProjectCreate
from app.services import artifact_service, job_service, project_service, viewer_service

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=Project, status_code=201)
def create_project(payload: ProjectCreate) -> Project:
    return project_service.create_project(payload)


@router.get("/{project_id}", response_model=Project)
def get_project(project_id: str) -> Project:
    project = project_service.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


@router.post("/{project_id}/jobs", response_model=Job, status_code=201)
def create_job(project_id: str, payload: JobCreate) -> Job:
    if project_service.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    return job_service.create_job(project_id, payload)


@router.get("/{project_id}/artifacts", response_model=ArtifactList)
def list_artifacts(project_id: str) -> ArtifactList:
    result = artifact_service.list_artifacts(project_id)
    if result is None:
        raise HTTPException(status_code=404, detail="project not found")
    return result


@router.get("/{project_id}/artifacts/download")
def download_artifact(project_id: str, path: str = Query(...)) -> FileResponse:
    if project_service.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    target = artifact_service.safe_resolve(project_id, path)
    if target is None:
        raise HTTPException(status_code=400, detail="invalid or missing path")
    return FileResponse(str(target), filename=target.name)


@router.get("/{project_id}/metadata")
def step_metadata(project_id: str) -> dict:
    """Parsed STEP geometry facts (bbox, counts) from the inspection report."""
    if project_service.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    insp = paths.project_dir(project_id) / "reports" / "inspection.txt"
    if not insp.exists():
        return {"available": False}
    try:
        doc = json.loads(insp.read_text())
        summary = doc["tokens"][0]["summary"]
        b = summary["bounds"]
        mn, mx = b["min"], b["max"]
        dims = [round(mx[i] - mn[i], 3) for i in range(3)]
        return {
            "available": True,
            "dimensions_mm": {"x": dims[0], "y": dims[1], "z": dims[2]},
            "bounds": b,
            "solids": summary.get("shapeCount"),
            "occurrences": summary.get("occurrenceCount"),
            "faces": summary.get("faceCount"),
            "edges": summary.get("edgeCount"),
            "kind": summary.get("kind"),
        }
    except Exception:  # noqa: BLE001 - report shape may vary
        return {"available": False}


@router.get("/{project_id}/viewer", response_model=ViewerResponse)
def viewer(project_id: str, path: str = Query(...)) -> ViewerResponse:
    if project_service.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    if artifact_service.safe_resolve(project_id, path) is None:
        raise HTTPException(status_code=400, detail="invalid or missing path")
    if not viewer_service.is_viewable(path):
        raise HTTPException(status_code=400, detail="path is not viewable")
    return ViewerResponse(
        project_id=project_id,
        path=path,
        viewer_url=viewer_service.viewer_url(project_id, path),
    )
