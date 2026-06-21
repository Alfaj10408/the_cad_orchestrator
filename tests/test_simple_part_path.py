import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import asyncio, json
from app.services import claude_generation as cg, claude_code_adapter, job_service
from app.core import paths

VALID_PART = ("from build123d import *\n\n"
              "def gen_step():\n"
              "    return fillet(Box(50,50,10).edges(), 2)\n")

def test_simple_part_uses_one_node_graph(monkeypatch):
    pid = "wire_simple"
    paths.ensure_project_skeleton(pid)
    (paths.project_dir(pid) / "brief.json").write_text(json.dumps({
        "project_id": pid, "prompt": "create a 3D mounting plate", "intent": "concept_cad",
        "parameters": {"dimensions": "120 x 80 x 6 mm", "units": "mm", "material": "PLA"},
        "user_answers": {"dimensions": "120 x 80 x 6 mm"}, "ready_to_generate": True,
        "generation_mode": "qwen_claude_code"}))
    job = job_service.create_job_full(pid, "generation", "CREATED")
    async def fake_run_claude(project_id, job_id, prompt, ch, **kw):
        # emulate Claude writing the single component source
        ws = claude_code_adapter.ensure_workspace(project_id)
        d = ws / "output" / "components" / "part"; d.mkdir(parents=True, exist_ok=True)
        (d / "generate.py").write_text(VALID_PART)
        return {"ok": True, "failure_class": None, "session_id": "s",
                "result_text": "done", "exit_code": 0, "error": None}
    monkeypatch.setattr(claude_code_adapter, "run_claude", fake_run_claude)
    asyncio.run(cg.run(pid, job.job_id))
    g = json.loads((paths.project_dir(pid) / "reports" / "assembly_graph.json").read_text())
    assert g["node_count"] == 1
    j = job_service.get_job(job.job_id)
    assert j.status == "COMPLETED"
