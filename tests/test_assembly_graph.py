import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.orchestrator import assembly_graph

MANIFEST = {"project_id": "p", "components": [
    {"name": "fuselage", "quantity": 1, "role": "body",
     "target_bbox_mm": {"x": 112.5, "y": 112.5, "z": 60.0},
     "source": "output/components/fuselage/generate.py",
     "step": "output/components/fuselage/fuselage.step"},
    {"name": "arm", "quantity": 4, "role": "boom",
     "target_bbox_mm": {"x": 125.0, "y": 20.0, "z": 9.6},
     "source": "output/components/arm/generate.py",
     "step": "output/components/arm/arm.step"},
]}
SPEC = {"project_id": "p", "object_class": "drone", "object_kind": "quadcopter drone",
        "overall_envelope_mm": {"x": 250.0, "y": 250.0, "z": 120.0}}

def test_quantity_expansion_and_fields():
    g = assembly_graph.build_graph(MANIFEST, SPEC)
    ids = [n["id"] for n in g["nodes"]]
    assert ids == ["fuselage", "arm_0", "arm_1", "arm_2", "arm_3"]
    assert g["node_count"] == 5
    arm0 = g["nodes"][1]
    assert arm0["component_type"] == "arm" and arm0["instance_index"] == 0
    assert arm0["parent"] == "root"
    assert arm0["step_file"] == "output/components/arm/arm.step"
    assert arm0["placement"] is None
    assert g["envelope_mm"] == {"x": 250.0, "y": 250.0, "z": 120.0}
