import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import json
from app.core import paths
from app.services import claude_code_adapter as adapter
from app.core import config as cfg
from app.orchestrator import component_validator as cv


def test_build_claude_argv_uses_passed_tools_and_turns():
    argv = adapter.build_claude_argv(prompt="hi", model="sonnet", max_turns=8,
                                     tools="Read,Write,Edit")
    assert "--tools" in argv
    assert argv[argv.index("--tools") + 1] == "Read,Write,Edit"
    assert "--max-turns" in argv
    assert argv[argv.index("--max-turns") + 1] == "8"
    assert "--model" in argv and argv[argv.index("--model") + 1] == "sonnet"
    assert argv[-1] == "hi"  # prompt is the final argv value
    assert "Bash" not in argv[argv.index("--tools") + 1]


def test_component_config_defaults():
    assert cfg.CLAUDE_CODE_COMPONENT_TOOLS == "Read,Write,Edit"
    assert "Bash" not in cfg.CLAUDE_CODE_COMPONENT_TOOLS
    assert cfg.CLAUDE_CODE_COMPONENT_MAX_TURNS == 12
    assert cfg.CLAUDE_CODE_COMPONENT_NEAR_CAP == 8
    # globals unchanged
    assert cfg.CLAUDE_CODE_TOOLS == "Read,Write,Edit,Bash"
    assert cfg.CLAUDE_CODE_MAX_TURNS == 15


_SPEC = {"object_class": "quadcopter drone assembly"}
_COMP = {"name": "fuselage", "role": "central body",
         "source": "output/components/fuselage/generate.py",
         "target_bbox_mm": {"x": 112.5, "y": 112.5, "z": 60.0}}


def test_component_prompt_has_preflight_and_path():
    p = cv.component_prompt(_SPEC, _COMP)
    assert "output/components/fuselage/generate.py" in p
    low = p.lower()
    assert "first" in low and "write" in low          # write-first directive
    assert "do not inspect" in low or "do not read or list" in low
    assert "plugins" in low                            # explicitly forbids plugin inspection
    assert "do not read" in low and "skill" in low     # forbids reading skill files
    assert "use the installed cad skill" not in low    # old skill-read line removed
    assert "def gen_step()" in p                       # build123d requirement preserved


def test_repair_prompt_is_edit_targeted_and_carries_error():
    r = cv.repair_prompt(_COMP, "NameError: name 'translate' is not defined")
    low = r.lower()
    assert "edit" in low
    assert "smallest" in low or "only the failing" in low
    assert "do not rewrite" in low or "do not execute" in low
    assert "NameError: name 'translate' is not defined" in r  # exact error included


def test_write_metrics_schema_and_totals():
    pid = "metrics_t"
    records = [
        {"name": "a", "attempts": 1, "repairs": 0, "turns_total": 4,
         "turns_per_attempt": [4], "duration_seconds": 12.5, "failure_class": None,
         "source_bytes": 1800, "valid": True, "reason": None},
        {"name": "b", "attempts": 2, "repairs": 1, "turns_total": 9,
         "turns_per_attempt": [5, 4], "duration_seconds": 30.0, "failure_class": None,
         "source_bytes": 1500, "valid": True, "reason": None},
    ]
    rep = cv.write_metrics(pid, records)
    assert rep["totals"]["components"] == 2
    assert rep["totals"]["passed"] == 2
    assert rep["totals"]["turns_total"] == 13
    assert rep["totals"]["repairs_total"] == 1
    assert round(rep["totals"]["avg_turns_per_component"], 1) == 6.5
    assert round(rep["totals"]["duration_total_s"], 1) == 42.5
    on_disk = json.loads((paths.project_dir(pid) / "reports" / "component_metrics.json").read_text())
    assert on_disk["components"][0]["name"] == "a"
    assert on_disk["components"][0]["duration_seconds"] == 12.5


# ---------------------------------------------------------------------------
# Task-6 integration: component loop wires tools/max_turns + writes metrics
# ---------------------------------------------------------------------------
import asyncio
from app.services import claude_generation as cg, claude_code_adapter, job_service

VALID_COMP = ("from build123d import *\n\n"
              "def gen_step():\n    return fillet(Box(40,40,28).edges(), 2)\n")


def _seed(pid, prompt, dims):
    paths.ensure_project_skeleton(pid)
    (paths.project_dir(pid) / "brief.json").write_text(json.dumps({
        "project_id": pid, "prompt": prompt, "intent": "concept_cad",
        "parameters": {"dimensions": dims, "units": "mm", "material": "PLA"},
        "user_answers": {"dimensions": dims}, "ready_to_generate": True,
        "generation_mode": "qwen_claude_code"}))


def test_component_loop_uses_component_tools_and_writes_metrics(monkeypatch):
    pid = "wire_comp_tools"
    _seed(pid, "create a 3D gear housing", "100 x 100 x 60 mm")
    job = job_service.create_job_full(pid, "generation", "CREATED")
    seen = {"tools": set(), "max_turns": set()}
    async def fake_run_claude(project_id, job_id, prompt, ch, *, tools=None,
                              max_turns=None, model=None, timeout=None):
        seen["tools"].add(tools); seen["max_turns"].add(max_turns)
        # emulate Claude writing the component source named in the prompt
        import re
        m = re.search(r"output/components/(\S+?)/generate\.py", prompt)
        rel = m.group(0)
        dst = claude_code_adapter.safe_workspace_path(
            claude_code_adapter.ensure_workspace(project_id), rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(VALID_COMP)
        return {"ok": True, "failure_class": None, "num_turns": 4,
                "session_id": "s", "result_text": "done", "exit_code": 0, "error": None}
    monkeypatch.setattr(claude_code_adapter, "run_claude", fake_run_claude)
    asyncio.run(cg.run(pid, job.job_id))
    assert seen["tools"] == {"Read,Write,Edit"}
    assert seen["max_turns"] == {12}
    metrics = json.loads((paths.project_dir(pid) / "reports" / "component_metrics.json").read_text())
    assert metrics["totals"]["components"] >= 1
    assert all("turns_total" in c for c in metrics["components"])
    assert all("duration_seconds" in c for c in metrics["components"])
    assert "duration_total_s" in metrics["totals"]
