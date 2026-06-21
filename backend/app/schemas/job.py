"""Job schemas."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class JobCreate(BaseModel):
    kind: Optional[str] = "text_to_cad"


class Job(BaseModel):
    job_id: str
    project_id: str
    kind: str = "text_to_cad"
    status: str = "CREATED"
    stage: Optional[str] = None
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
