"""Viewer URL generation for CAD/mesh artifacts.

`viewer_url` targets the real repo CAD viewer (vite), which scans a local
filesystem root via `?dir=` and opens a directory-relative `?file=`.
`fallback_viewer_url` is the temporary backend landing page (/viewer).
"""
from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import quote

from app.core.config import VIEWER_BASE_URL
from app.core import paths

# Extensions the viewer can render.
VIEWABLE_EXTS = {".step", ".stp", ".glb", ".stl", ".3mf", ".dxf"}


def is_viewable(relative_path: str) -> bool:
    return PurePosixPath(relative_path).suffix.lower() in VIEWABLE_EXTS


def viewer_url(project_id: str, relative_path: str) -> str:
    """Real repo CAD viewer URL (?dir=<project root>&file=<relative path>)."""
    project_dir = str(paths.project_dir(project_id).resolve())
    return (
        f"{VIEWER_BASE_URL}/?dir={quote(project_dir)}"
        f"&file={quote(relative_path)}"
    )


def fallback_viewer_url(project_id: str, relative_path: str) -> str:
    """Temporary backend landing page URL."""
    return f"/viewer?project_id={project_id}&path={relative_path}"
