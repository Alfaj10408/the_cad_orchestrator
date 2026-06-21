"""Artifact schemas."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class Artifact(BaseModel):
    relative_path: str          # relative to project folder
    name: str
    category: str               # source | cad | mesh | snapshot | report | other
    size_bytes: int
    download_url: str
    viewer_url: Optional[str] = None


class ArtifactList(BaseModel):
    project_id: str
    artifacts: List[Artifact]


class ViewerResponse(BaseModel):
    project_id: str
    path: str
    viewer_url: str
