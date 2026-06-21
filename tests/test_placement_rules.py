import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import math
from app.orchestrator import assembly_graph, placement_rules

MAN = {"project_id": "p", "components": [
    {"name": "fuselage", "quantity": 1, "role": "body",
     "target_bbox_mm": {"x": 1, "y": 1, "z": 1}, "step": "output/components/fuselage/fuselage.step"},
    {"name": "arm", "quantity": 4, "role": "boom",
     "target_bbox_mm": {"x": 1, "y": 1, "z": 1}, "step": "output/components/arm/arm.step"},
]}
DRONE = {"project_id": "p", "object_kind": "quadcopter drone", "object_class": "drone",
         "overall_envelope_mm": {"x": 250.0, "y": 250.0, "z": 120.0}}
GENERIC = {"project_id": "p", "object_kind": "widget", "object_class": "widget",
           "overall_envelope_mm": {"x": 100.0, "y": 100.0, "z": 100.0}}

def test_drone_arms_radial_unique_angles():
    g = placement_rules.resolve(assembly_graph.build_graph(MAN, DRONE), DRONE)
    arms = [n for n in g["nodes"] if n["component_type"] == "arm"]
    angles = sorted(n["placement"]["rotate_deg"][2] for n in arms)
    assert angles == [45, 135, 225, 315]
    assert all(n["placement"]["rule"] == "radial_arm" for n in arms)
    assert g["placement_engine"] == "domain:quadcopter drone"

def test_generic_grid_is_filled_and_centered():
    g = placement_rules.resolve(assembly_graph.build_graph(MAN, GENERIC), GENERIC)
    assert g["placement_engine"] == "generic_layout"
    assert all(n["placement"] is not None for n in g["nodes"])
    xs = [n["placement"]["translate_mm"][0] for n in g["nodes"]]
    assert min(xs) <= 0 <= max(xs)  # centered around origin
