"""Artifact endpoints.

Project-scoped artifact listing lives in app.api.projects
(GET /projects/{project_id}/artifacts). This router is reserved
for future flat artifact access.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/artifacts", tags=["artifacts"])
