"""Deterministic assembly instance graph from the component manifest."""
from __future__ import annotations
import json
from app.core import paths


def build_graph(manifest: dict, design_spec: dict) -> dict:
    nodes = []
    for comp in manifest["components"]:
        qty = int(comp.get("quantity", 1) or 1)
        for i in range(qty):
            nid = comp["name"] if qty == 1 else f"{comp['name']}_{i}"
            nodes.append({
                "id": nid,
                "component_type": comp["name"],
                "instance_index": i,
                "parent": "root",
                "role": comp.get("role", ""),
                "step_file": comp["step"],
                "target_bbox_mm": comp.get("target_bbox_mm", {}),
                "placement": None,
            })
    env = design_spec["overall_envelope_mm"]
    return {
        "project_id": design_spec.get("project_id") or manifest.get("project_id", ""),
        "object_class": design_spec.get("object_class", ""),
        "object_kind": design_spec.get("object_kind", ""),
        "frame": {"id": "root", "origin_mm": [0, 0, 0],
                  "convention": "XY base, +Z up, mm"},
        "nodes": nodes,
        "node_count": len(nodes),
        "envelope_mm": env,
        "placement_engine": None,
    }


def write_graph(project_id: str, graph: dict) -> str:
    reports = paths.project_dir(project_id) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    out = reports / "assembly_graph.json"
    out.write_text(json.dumps(graph, indent=2))
    return str(out)
