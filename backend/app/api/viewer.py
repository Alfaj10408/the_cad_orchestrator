"""Temporary viewer landing page + safe file preview (no /api prefix).

GET /viewer?project_id=...&path=cad/model.step  -> HTML landing page
GET /preview/{project_id}/{path:path}           -> safe file serving
"""
from __future__ import annotations

import html
from pathlib import PurePosixPath
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse

from app.services import artifact_service, project_service, viewer_service

router = APIRouter(tags=["viewer"])


def _file_type(path: str) -> str:
    return PurePosixPath(path).suffix.lower().lstrip(".").upper() or "FILE"


@router.get("/viewer", response_class=HTMLResponse)
def viewer_page(project_id: str = Query(...), path: str = Query(...)) -> HTMLResponse:
    if project_service.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    if artifact_service.safe_resolve(project_id, path) is None:
        raise HTTPException(status_code=400, detail="invalid or missing path")
    if not viewer_service.is_viewable(path):
        raise HTTPException(status_code=400, detail="path is not viewable")

    safe_path = html.escape(path)
    safe_pid = html.escape(project_id)
    ftype = _file_type(path)
    download = (
        f"/api/projects/{project_id}/artifacts/download?path={quote(path)}"
    )
    artifacts_link = f"/api/projects/{project_id}/artifacts"
    real_viewer = html.escape(viewer_service.viewer_url(project_id, path))

    snapshot_block = ""
    if artifact_service.safe_resolve(project_id, "cad/snapshot.png") is not None:
        snap_src = f"/preview/{project_id}/cad/snapshot.png"
        snapshot_block = (
            f'<h2>Snapshot</h2><img src="{snap_src}" '
            f'alt="snapshot" style="max-width:640px;border:1px solid #ccc"/>'
        )

    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Text-to-CAD Viewer</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #222; }}
  code {{ background:#f4f4f4; padding:2px 4px; border-radius:3px; }}
  .note {{ margin-top:1.5rem; padding:0.75rem 1rem; background:#fff7e6;
           border:1px solid #ffd591; border-radius:4px; }}
  a {{ color:#1565c0; }}
</style>
</head>
<body>
  <h1>Text-to-CAD Viewer</h1>
  <p><strong>Project:</strong> <code>{safe_pid}</code></p>
  <p><strong>File:</strong> <code>{safe_path}</code></p>
  <p><strong>Type:</strong> {ftype}</p>
  <p>
    <a href="{real_viewer}" target="_blank" rel="noopener">Open interactive CAD viewer ↗</a>
  </p>
  <p>
    <a href="{download}">Download file</a> &nbsp;|&nbsp;
    <a href="{artifacts_link}">Artifact list</a>
  </p>
  {snapshot_block}
  <div class="note">Interactive viewer opens the repo CAD Viewer
    (must be running — see scripts/run_repo_viewer.sh). Snapshot below is the
    static fallback.</div>
</body>
</html>"""
    return HTMLResponse(content=body)


@router.get("/preview/{project_id}/{path:path}")
def preview_file(project_id: str, path: str) -> FileResponse:
    if project_service.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    target = artifact_service.safe_resolve(project_id, path)
    if target is None:
        raise HTTPException(status_code=400, detail="invalid or missing path")
    return FileResponse(str(target))
