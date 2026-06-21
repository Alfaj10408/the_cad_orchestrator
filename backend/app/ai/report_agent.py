"""Output inspection agent.

After CAD inspection, assemble structured findings from the export/mesh/inspect
results and the inspection report. Deterministic core; when the orchestrator is
enabled it adds a short interpretive note. Writes reports/findings.json.
"""
from __future__ import annotations

import json

from app.ai import orchestrator
from app.ai.llm import config as orch_config
from app.ai.llm.client import OrchestratorError
from app.core import paths


def inspect(project_id: str, step: dict, meshes: dict, insp: dict) -> dict:
    """Build findings dict from stage results + inspection report; persist it."""
    root = paths.project_dir(project_id)
    report_path = root / (insp.get("report") or "reports/inspection.txt")
    report_text = report_path.read_text() if report_path.exists() else ""

    issues: list[str] = []
    if not step.get("ok"):
        issues.append("STEP export failed")
    if not meshes.get("ok"):
        issues.append("mesh export failed")
    if not insp.get("ok"):
        issues.append("inspection failed")
    if not report_text.strip():
        issues.append("empty inspection report")

    findings: dict = {
        "step_ok": bool(step.get("ok")),
        "mesh_ok": bool(meshes.get("ok")),
        "inspect_ok": bool(insp.get("ok")),
        "has_inspection_report": bool(report_text.strip()),
        "issues": issues,
        "inspection_excerpt": report_text[:2000],
    }

    if orch_config.ORCHESTRATOR_ENABLED:
        try:
            findings["note"] = orchestrator.inspect_report(findings)
        except OrchestratorError as exc:
            findings["note_error"] = str(exc)

    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "findings.json").write_text(json.dumps(findings, indent=2))
    return findings
