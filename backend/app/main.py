"""FastAPI application entrypoint (MVP v1 skeleton)."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

from app.core.config import API_PREFIX, APP_TITLE, APP_VERSION, PRODUCT_ROOT, V1_CORS_ORIGINS
from app.services import claude_code_adapter
from app.v1 import db as v1db, routes as v1routes
from app.v1.queue import JobQueue


@asynccontextmanager
async def _lifespan(app):
    conn = v1db.connect(); v1db.init_db(conn)
    app.state.db = conn
    q = JobQueue(conn); q.recover(conn); q.start()
    app.state.queue = q
    try:
        yield
    finally:
        await q.stop()
        await claude_code_adapter.shutdown()


app = FastAPI(title=APP_TITLE, version=APP_VERSION, lifespan=_lifespan)

if V1_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=V1_CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

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

# /v1 production API router (prefix /v1 is defined in the router itself).
app.include_router(v1routes.router)

# Single-port serving: mount the built frontend last so /api + /viewer win.
_FRONTEND_DIST = PRODUCT_ROOT / "frontend" / "dist"
if _FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")
