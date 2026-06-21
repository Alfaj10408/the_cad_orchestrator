"""FastAPI application entrypoint (MVP v1 skeleton)."""
from fastapi import FastAPI

from app.api import (
    artifacts,
    clarification,
    events,
    health,
    jobs,
    orchestrator,
    projects,
    viewer,
    workers,
)
from fastapi.staticfiles import StaticFiles

from app.core.config import API_PREFIX, APP_TITLE, APP_VERSION, PRODUCT_ROOT
from app.services import claude_code_adapter

app = FastAPI(title=APP_TITLE, version=APP_VERSION)

app.include_router(health.router, prefix=API_PREFIX)
app.include_router(projects.router, prefix=API_PREFIX)
app.include_router(clarification.router, prefix=API_PREFIX)
app.include_router(orchestrator.router, prefix=API_PREFIX)
app.include_router(events.router, prefix=API_PREFIX)
app.include_router(jobs.router, prefix=API_PREFIX)
app.include_router(artifacts.router, prefix=API_PREFIX)
app.include_router(workers.router, prefix=API_PREFIX)
# Viewer + preview routes are mounted at root (no /api prefix).
app.include_router(viewer.router)


@app.on_event("shutdown")
async def _cleanup_claude() -> None:
    """Kill any orphaned Claude Code processes on backend shutdown."""
    await claude_code_adapter.shutdown()


# Single-port serving: mount the built frontend last so /api + /viewer win.
_FRONTEND_DIST = PRODUCT_ROOT / "frontend" / "dist"
if _FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")
