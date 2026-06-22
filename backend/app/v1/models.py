from __future__ import annotations
from pydantic import BaseModel, Field

class JobCreate(BaseModel):
    prompt: str = Field(min_length=1)
    dimensions: str | None = None
    material: str = "PLA"
    mode: str = "qwen_claude_code"

class JobView(BaseModel):
    job_id: str
    status: str
    stage: str | None = None
    failure_class: str | None = None
    queue_pos: int | None = None
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    artifacts_available: bool | None = None
