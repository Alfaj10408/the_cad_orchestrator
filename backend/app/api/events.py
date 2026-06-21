"""Real-time generation event streaming (SSE), cancellation, job status."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.schemas.events import TERMINAL_TYPES, GenEvent
from app.services import claude_code_adapter, event_service, job_service

router = APIRouter(prefix="/projects", tags=["events"])

HEARTBEAT_SECONDS = 12


def _sse(ev: GenEvent) -> str:
    return f"id: {ev.id}\nevent: {ev.type}\ndata: {ev.model_dump_json()}\n\n"


def _heartbeat() -> str:
    payload = json.dumps({"type": "heartbeat"})
    return f"event: heartbeat\ndata: {payload}\n\n"


async def _gen(project_id: str, job_id: str, last_id: int, request: Request):
    # No live channel: replay persisted events then end.
    if not event_service.channel_exists(job_id):
        for ev in event_service._read_jsonl(project_id, job_id, last_id):
            yield _sse(ev)
        return

    ch = event_service.get_channel(project_id, job_id)
    q = ch.subscribe()
    seen = last_id
    try:
        for ev in ch.replay(last_id):
            seen = max(seen, ev.id)
            yield _sse(ev)
        if ch.terminal:
            return
        while True:
            if await request.is_disconnected():
                return
            try:
                ev = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield _heartbeat()
                continue
            if ev.id <= seen:
                continue
            seen = ev.id
            yield _sse(ev)
            if ev.type in TERMINAL_TYPES:
                return
    finally:
        ch.unsubscribe(q)


@router.get("/{project_id}/jobs/{job_id}/events")
async def stream_events(
    project_id: str,
    job_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    job = job_service.get_job(job_id)
    if job is None or job.project_id != project_id:
        raise HTTPException(status_code=404, detail="job not found")
    try:
        last_id = int(last_event_id) if last_event_id else 0
    except ValueError:
        last_id = 0
    return StreamingResponse(
        _gen(project_id, job_id, last_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{project_id}/jobs/{job_id}/cancel")
def cancel_job(project_id: str, job_id: str) -> dict:
    job = job_service.get_job(job_id)
    if job is None or job.project_id != project_id:
        raise HTTPException(status_code=404, detail="job not found")
    killed = claude_code_adapter.cancel(job_id)
    return {"job_id": job_id, "requested": True, "killed_process": killed}


@router.get("/{project_id}/jobs/{job_id}")
def get_job(project_id: str, job_id: str) -> dict:
    job = job_service.get_job(job_id)
    if job is None or job.project_id != project_id:
        raise HTTPException(status_code=404, detail="job not found")
    return job.model_dump()
