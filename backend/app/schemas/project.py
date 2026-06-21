"""Project schemas."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ProjectCreate(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None


class Project(BaseModel):
    project_id: str
    name: Optional[str] = None
    prompt: Optional[str] = None
    status: str = "CREATED"
    created_at: str
