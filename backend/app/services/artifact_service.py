"""Artifact service: listing, categories, and safe path resolution."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from app.core import paths
from app.schemas.artifact import Artifact, ArtifactList
from app.services import viewer_service


def _category(rel: str) -> str:
    p = rel.replace("\\", "/")
    suffix = Path(rel).suffix.lower()
    if p.startswith("source/"):
        return "source"
    if suffix in (".stl", ".glb", ".3mf", ".obj"):
        return "mesh"
    if suffix in (".step", ".stp"):
        return "cad"
    if suffix == ".png" or "snapshot" in Path(rel).name:
        return "snapshot"
    if p.startswith("reports/"):
        return "report"
    return "other"


def download_url(project_id: str, rel: str) -> str:
    return (
        f"/api/projects/{project_id}/artifacts/download?path={quote(rel)}"
    )


def safe_resolve(project_id: str, rel_path: str) -> Path | None:
    """Resolve rel_path inside the project folder. Returns None if unsafe.

    Rejects absolute paths, parent traversal, and anything escaping the
    project root. Returns the resolved Path only when it exists as a file.
    """
    if not rel_path:
        return None
    candidate = Path(rel_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None

    root = paths.project_dir(project_id).resolve()
    if not root.exists():
        return None
    target = (root / candidate).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    if not target.is_file():
        return None
    return target


def _build(project_id: str, rel: str, size: int) -> Artifact:
    viewable = viewer_service.is_viewable(rel)
    return Artifact(
        relative_path=rel,
        name=Path(rel).name,
        category=_category(rel),
        size_bytes=size,
        download_url=download_url(project_id, rel),
        viewer_url=viewer_service.viewer_url(project_id, rel) if viewable else None,
    )


def list_artifacts(project_id: str) -> ArtifactList | None:
    root = paths.project_dir(project_id)
    if not root.exists():
        return None
    items: list[Artifact] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.name.startswith("."):
            rel = str(path.relative_to(root))
            items.append(_build(project_id, rel, path.stat().st_size))
    return ArtifactList(project_id=project_id, artifacts=items)


def url_maps(project_id: str) -> tuple[dict, dict]:
    """Return (artifact_urls, viewer_urls) for all current files."""
    root = paths.project_dir(project_id)
    artifact_urls: dict = {}
    viewer_urls: dict = {}
    if not root.exists():
        return artifact_urls, viewer_urls
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.name.startswith("."):
            rel = str(path.relative_to(root))
            artifact_urls[rel] = download_url(project_id, rel)
            if viewer_service.is_viewable(rel):
                viewer_urls[rel] = viewer_service.viewer_url(project_id, rel)
    return artifact_urls, viewer_urls
