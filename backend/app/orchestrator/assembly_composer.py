"""Emit a deterministic build123d assembly from validated component STEPs. Zero Claude."""
from __future__ import annotations
from app.services import claude_code_adapter

_HEADER = "from build123d import *\n\n\ndef gen_step():\n    parts = []\n"


def emit_source(project_id: str, graph: dict) -> str:
    ws = claude_code_adapter.workspace_dir(project_id)
    lines = [_HEADER]
    for node in graph["nodes"]:
        abs_step = claude_code_adapter.safe_workspace_path(ws, node["step_file"])
        if abs_step is None:
            raise ValueError(f"unsafe/missing step path for {node['id']}: {node['step_file']}")
        p = node["placement"]; t = p["translate_mm"]; r = p["rotate_deg"]
        lines.append(
            f"    _p = import_step({str(abs_step)!r})\n"
            f"    _p = _p.moved(Location(({t[0]}, {t[1]}, {t[2]}), ({r[0]}, {r[1]}, {r[2]})))\n"
            f"    _p.label = {node['id']!r}\n"
            f"    parts.append(_p)\n"
        )
    lines.append("    return Compound(children=parts)\n")
    return "".join(lines)


def write_source(project_id: str, graph: dict) -> str:
    ws = claude_code_adapter.ensure_workspace(project_id)
    src = emit_source(project_id, graph)
    (ws / "output" / "generate.py").write_text(src)
    return src
