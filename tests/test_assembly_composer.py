import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.orchestrator import assembly_graph, placement_rules, assembly_composer
from app.services import claude_code_adapter

MAN = {"project_id": "comp_x", "components": [
    {"name": "fuselage", "quantity": 1, "role": "body",
     "target_bbox_mm": {"x": 1, "y": 1, "z": 1}, "step": "output/components/fuselage/fuselage.step"},
    {"name": "arm", "quantity": 2, "role": "boom",
     "target_bbox_mm": {"x": 1, "y": 1, "z": 1}, "step": "output/components/arm/arm.step"},
]}
SPEC = {"project_id": "comp_x", "object_kind": "quadcopter drone",
        "object_class": "drone", "overall_envelope_mm": {"x": 250.0, "y": 250.0, "z": 120.0}}

def test_emit_source_uses_absolute_steps_and_compound():
    g = placement_rules.resolve(assembly_graph.build_graph(MAN, SPEC), SPEC)
    src = assembly_composer.emit_source("comp_x", g)
    ws = str(claude_code_adapter.workspace_dir("comp_x"))
    assert "def gen_step():" in src
    assert "from build123d import *" in src
    assert src.count("import_step(") == 3            # 1 fuselage + 2 arms
    assert ws in src                                 # absolute workspace path embedded
    assert "Compound(children=parts)" in src
    assert "os" not in src and "subprocess" not in src
