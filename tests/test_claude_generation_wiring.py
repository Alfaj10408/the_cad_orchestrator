import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import asyncio, json, types
import pytest
from app.services import claude_generation as cg, claude_code_adapter, job_service
from app.core import paths


def _seed_project(pid, kind="quadcopter drone"):
    paths.ensure_project_skeleton(pid)
    (paths.project_dir(pid) / "brief.json").write_text(json.dumps({
        "project_id": pid, "prompt": "create a 3D drone", "intent": "concept_cad",
        "parameters": {"dimensions": "250 x 250 x 120 mm", "units": "mm", "material": "PLA"},
        "user_answers": {"dimensions": "250 x 250 x 120 mm"}, "ready_to_generate": True,
        "generation_mode": "qwen_claude_code"}))


def test_quota_aborts_without_repair(monkeypatch):
    pid = "wire_quota"; _seed_project(pid)
    job = job_service.create_job_full(pid, "generation", "CREATED")
    calls = {"n": 0}
    async def fake_run_claude(project_id, job_id, prompt, ch, **kw):
        calls["n"] += 1
        return {"ok": False, "failure_class": "quota", "error": "session limit",
                "session_id": None, "result_text": "You've hit your session limit",
                "exit_code": 1}
    monkeypatch.setattr(claude_code_adapter, "run_claude", fake_run_claude)
    asyncio.run(cg.run(pid, job.job_id))
    j = job_service.get_job(job.job_id)
    assert j.status == "FAILED_QUOTA"
    assert calls["n"] == 1   # aborted on first component, no repair spin
