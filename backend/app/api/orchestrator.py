"""Orchestrator endpoints (workflow planning)."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from app.ai import router as stage_router
from app.core import paths
from app.schemas.orchestrator import NextAction
from app.services import project_service

router = APIRouter(prefix="/projects", tags=["orchestrator"])


@router.post("/{project_id}/orchestrate/plan", response_model=NextAction)
def plan_next(project_id: str) -> NextAction:
    """Decide which pipeline should run next for an analyzed project."""
    if project_service.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")

    brief_path = paths.project_dir(project_id) / "brief.json"
    if not brief_path.exists():
        raise HTTPException(status_code=409, detail="project is not analyzed yet")
    brief = json.loads(brief_path.read_text())

    return stage_router.decide_next(brief)
