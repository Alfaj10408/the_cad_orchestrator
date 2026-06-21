"""Filesystem path helpers for projects."""
from __future__ import annotations

from pathlib import Path

from app.core.config import PROJECTS_ROOT, PROJECT_SUBDIRS


def project_dir(project_id: str) -> Path:
    return PROJECTS_ROOT / project_id


def metadata_path(project_id: str) -> Path:
    return project_dir(project_id) / "metadata.json"


def jobs_dir(project_id: str) -> Path:
    return project_dir(project_id) / "jobs"


def job_path(project_id: str, job_id: str) -> Path:
    return jobs_dir(project_id) / f"{job_id}.json"


def ensure_project_skeleton(project_id: str) -> Path:
    """Create the project folder and standard subfolders."""
    root = project_dir(project_id)
    root.mkdir(parents=True, exist_ok=True)
    for sub in PROJECT_SUBDIRS:
        (root / sub).mkdir(exist_ok=True)
    jobs_dir(project_id).mkdir(exist_ok=True)
    return root
