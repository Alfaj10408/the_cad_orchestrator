"""Orchestrator integration tests (no live LLM server).

The Qwen client is stubbed, so these run offline. Compatible with pytest and
also runnable standalone:  python tests/test_orchestrator.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# Make the backend package importable when run standalone.
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.ai import cad_agent, orchestrator, repair_agent, report_agent  # noqa: E402
from app.ai import router as stage_router  # noqa: E402
from app.ai.llm import config as orch_config  # noqa: E402
from app.ai.llm.client import OrchestratorError  # noqa: E402
from app.core import paths  # noqa: E402
from app.schemas.orchestrator import ANALYSIS_SCHEMA, Pipeline  # noqa: E402


def _set_enabled(value: bool) -> None:
    orch_config.ORCHESTRATOR_ENABLED = value


def test_analysis_schema_required_keys():
    for key in ("intent", "confidence", "missing", "questions"):
        assert key in ANALYSIS_SCHEMA["properties"]


def test_analyze_maps_and_corrects_ready():
    orchestrator.chat_json = lambda s, u, sch: {
        "intent": "concept_cad",
        "confidence": 0.8,
        "signals": ["keyword:bracket"],
        "parameters": {"has_dimensions": False},
        "missing": ["dimensions"],
        "questions": [{"id": "dimensions", "question": "Size?", "required": True}],
        "summary": "bracket",
        "assumptions": [],
        "ready_to_generate": True,  # must be corrected to False (missing non-empty)
    }
    a = orchestrator.analyze("an L bracket")
    assert a.intent.value == "concept_cad"
    assert a.ready_to_generate is False
    assert a.questions[0].id == "dimensions"


def test_cad_agent_deterministic_and_fallback():
    brief = {"prompt": "bracket", "intent": "concept_cad", "summary": "b",
             "parameters": {}, "assumptions": [], "user_answers": {}}
    _set_enabled(False)
    txt, src = cad_agent.build_worker_prompt(brief)
    assert src == "deterministic" and "Generate the build123d source now." in txt

    _set_enabled(True)
    orchestrator.chat = lambda s, u: "SPEC: box 50x50x5mm single solid."
    txt2, src2 = cad_agent.build_worker_prompt(brief)
    assert src2 == "orchestrator" and "SPEC" in txt2

    def boom(s, u):
        raise OrchestratorError("down")
    orchestrator.chat = boom
    txt3, src3 = cad_agent.build_worker_prompt(brief)
    assert src3 == "deterministic"
    _set_enabled(False)


def test_repair_agent_policy():
    assert repair_agent.decide({}, "err", 0, 2).action == "repair"
    assert repair_agent.decide({}, "err", 2, 2).action == "abort"

    _set_enabled(True)
    orchestrator.chat_json = lambda s, u, sch: {"action": "abort", "reason": "env", "hint": ""}
    assert repair_agent.decide({"summary": "x"}, "ImportError", 0, 2).action == "abort"
    _set_enabled(False)


def test_report_agent_findings(tmp_path: Path | None = None):
    tmp = tmp_path or Path(tempfile.mkdtemp())
    orig = paths.project_dir
    paths.project_dir = lambda pid: tmp
    try:
        (tmp / "reports").mkdir(parents=True, exist_ok=True)
        (tmp / "reports" / "inspection.txt").write_text("solids: 1")
        ok = report_agent.inspect("p", {"ok": True}, {"ok": True},
                                  {"ok": True, "report": "reports/inspection.txt"})
        assert ok["issues"] == [] and ok["has_inspection_report"]
        assert (tmp / "reports" / "findings.json").exists()
        bad = report_agent.inspect("p", {"ok": False}, {"ok": True},
                                   {"ok": True, "report": "reports/inspection.txt"})
        assert "STEP export failed" in bad["issues"]
    finally:
        paths.project_dir = orig


def test_router_branches_and_active_gate():
    _set_enabled(False)
    a = stage_router.decide_next({"intent": "concept_cad", "ready_to_generate": True})
    assert a.pipeline == Pipeline.mvp1_text_to_cad and a.supported

    b = stage_router.decide_next({"intent": "robotics_urdf", "ready_to_generate": True})
    assert b.pipeline == Pipeline.mvp2_robotics and b.supported is False

    c = stage_router.decide_next({"intent": "concept_cad", "ready_to_generate": False})
    assert c.pipeline == Pipeline.clarify and c.needs_clarification

    _set_enabled(True)
    orchestrator.chat_json = lambda s, u, sch: {
        "pipeline": "mvp4_printing", "needs_clarification": False, "reason": "gcode"}
    d = stage_router.decide_next({"intent": "unknown", "ready_to_generate": True})
    assert d.pipeline == Pipeline.mvp4_printing and d.supported is False
    _set_enabled(False)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
