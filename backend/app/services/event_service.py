"""In-memory + JSONL event bus for real-time generation streaming.

Per-job channels fan out normalized GenEvents to SSE subscribers and persist
every event to a JSONL file so reconnecting clients can replay missed events
(honoring Last-Event-ID). Async-native; never blocks the event loop for I/O
beyond small line appends.
"""
from __future__ import annotations

import asyncio
import json
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Optional

from app.core import paths
from app.schemas.events import TERMINAL_TYPES, GenEvent

# Bounded in-memory history per job (older events still available via JSONL).
_HISTORY_MAX = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def events_path(project_id: str, job_id: str) -> Path:
    return paths.jobs_dir(project_id) / f"{job_id}.events.jsonl"


class JobChannel:
    def __init__(self, project_id: str, job_id: str):
        self.project_id = project_id
        self.job_id = job_id
        self._seq = 0
        self._history: deque[GenEvent] = deque(maxlen=_HISTORY_MAX)
        self._subscribers: set[asyncio.Queue[GenEvent]] = set()
        self.terminal = False
        self._lock = asyncio.Lock()

    def _next_id(self) -> int:
        self._seq += 1
        return self._seq

    async def publish(
        self,
        source: str,
        type: str,
        *,
        stage: Optional[str] = None,
        message: Optional[str] = None,
        delta: Optional[str] = None,
        data: Optional[dict] = None,
    ) -> GenEvent:
        async with self._lock:
            ev = GenEvent(
                id=self._next_id(),
                project_id=self.project_id,
                job_id=self.job_id,
                timestamp=_now(),
                source=source,
                type=type,
                stage=stage,
                message=message,
                delta=delta,
                data=data,
            )
            self._history.append(ev)
            self._persist(ev)
            if ev.type in TERMINAL_TYPES:
                self.terminal = True
        # Fan out outside the lock.
        for q in list(self._subscribers):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass
        return ev

    def _persist(self, ev: GenEvent) -> None:
        p = events_path(self.project_id, self.job_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as fh:
            fh.write(ev.model_dump_json() + "\n")

    def replay(self, after_id: int) -> list[GenEvent]:
        """Events with id > after_id, from memory or JSONL fallback."""
        mem = [e for e in self._history if e.id > after_id]
        if mem and mem[0].id <= after_id + 1:
            return mem
        # Cold/partial: read full history from disk.
        return _read_jsonl(self.project_id, self.job_id, after_id)

    def subscribe(self) -> asyncio.Queue[GenEvent]:
        q: asyncio.Queue[GenEvent] = asyncio.Queue(maxsize=1000)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[GenEvent]) -> None:
        self._subscribers.discard(q)


_channels: dict[str, JobChannel] = {}


def get_channel(project_id: str, job_id: str) -> JobChannel:
    if job_id not in _channels:
        _channels[job_id] = JobChannel(project_id, job_id)
    return _channels[job_id]


def channel_exists(job_id: str) -> bool:
    return job_id in _channels


def _read_jsonl(project_id: str, job_id: str, after_id: int) -> list[GenEvent]:
    p = events_path(project_id, job_id)
    if not p.exists():
        return []
    out: list[GenEvent] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = GenEvent(**json.loads(line))
        except Exception:
            continue
        if ev.id > after_id:
            out.append(ev)
    return out


def is_terminal_persisted(project_id: str, job_id: str) -> bool:
    """Check JSONL for a terminal event (used when no live channel exists)."""
    for ev in _read_jsonl(project_id, job_id, 0):
        if ev.type in TERMINAL_TYPES:
            return True
    return False


async def stream(
    project_id: str, job_id: str, last_event_id: int = 0
) -> AsyncIterator[GenEvent]:
    """Yield replayed-then-live events until the job reaches a terminal state.

    If there is no live channel but a persisted JSONL exists (job already
    finished), replay it and stop.
    """
    if not channel_exists(job_id):
        for ev in _read_jsonl(project_id, job_id, last_event_id):
            yield ev
        return

    ch = get_channel(project_id, job_id)
    q = ch.subscribe()
    try:
        seen = last_event_id
        for ev in ch.replay(last_event_id):
            seen = max(seen, ev.id)
            yield ev
        if ch.terminal:
            return
        while True:
            ev = await q.get()
            if ev.id <= seen:
                continue
            seen = ev.id
            yield ev
            if ev.type in TERMINAL_TYPES:
                return
    finally:
        ch.unsubscribe(q)
