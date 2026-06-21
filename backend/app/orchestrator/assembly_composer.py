"""Emit a deterministic build123d assembly from validated component STEPs. Zero Claude."""
from __future__ import annotations
from app.services import claude_code_adapter

# Use OCC-level compound builder to avoid a build123d segfault when
# Compound(children=[...]) is called on import_step() results inside the
# cadpy step CLI execution context.
_HEADER = (
    "from build123d import *\n"
    "from OCP.TopoDS import TopoDS_Builder, TopoDS_Compound\n"
    "\n\n"
    "def gen_step():\n"
    "    parts = []\n"
)
_FOOTER = (
    "    _b = TopoDS_Builder(); _c = TopoDS_Compound(); _b.MakeCompound(_c)\n"
    "    for _s in parts: _b.Add(_c, _s.wrapped)\n"
    "    return Compound(_c)\n"
)


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
    lines.append(_FOOTER)
    return "".join(lines)


def write_source(project_id: str, graph: dict) -> str:
    ws = claude_code_adapter.ensure_workspace(project_id)
    src = emit_source(project_id, graph)
    (ws / "output" / "generate.py").write_text(src)
    return src
