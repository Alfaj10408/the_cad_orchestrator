"""Normalized generation event schema (streamed to the frontend via SSE).

Raw Claude stream-json is never sent to the browser; it is normalized into
these safe events. Auth/config data is never included.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

# Event sources.
SOURCE_SYSTEM = "system"
SOURCE_QWEN = "qwen"
SOURCE_CLAUDE = "claude_code"
SOURCE_WORKER = "cad_worker"
SOURCE_ARTIFACT = "artifact"

# Canonical event type vocabulary.
EVENT_TYPES = {
    "job.queued",
    "job.started",
    "planner.started",
    "planner.delta",
    "planner.completed",
    "claude.started",
    "text.delta",
    "tool.started",
    "tool.completed",
    "file.created",
    "file.updated",
    "cad.execution.started",
    "cad.execution.log",
    "cad.execution.completed",
    "artifact.created",
    "job.completed",
    "job.failed",
    "job.cancelled",
    "heartbeat",
}

TERMINAL_TYPES = {"job.completed", "job.failed", "job.cancelled"}


class GenEvent(BaseModel):
    id: int
    project_id: str
    job_id: str
    timestamp: str
    source: str
    type: str
    stage: Optional[str] = None
    message: Optional[str] = None
    delta: Optional[str] = None
    # Small, safe structured payload (tool name, file path, artifact info…).
    data: Optional[dict[str, Any]] = None
