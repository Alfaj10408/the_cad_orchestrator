"""Project service: JSON file storage."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from app.core import paths
from app.schemas.project import Project, ProjectCreate


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_project(payload: ProjectCreate) -> Project:
    project_id = uuid.uuid4().hex
    paths.ensure_project_skeleton(project_id)
    project = Project(
        project_id=project_id,
        name=payload.name,
        prompt=payload.prompt,
        status="CREATED",
        created_at=_now(),
    )
    paths.metadata_path(project_id).write_text(project.model_dump_json(indent=2))
    return project


def get_project(project_id: str) -> Project | None:
    meta = paths.metadata_path(project_id)
    if not meta.exists():
        return None
    return Project(**json.loads(meta.read_text()))
