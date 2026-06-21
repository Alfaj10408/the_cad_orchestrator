"""Deterministic component placement transforms. Zero Claude."""
from __future__ import annotations
import math


def _drone_xform(node: dict, env: dict) -> tuple[list, list, str]:
    name = node["component_type"]; i = node["instance_index"]
    x, y, z = env["x"], env["y"], env["z"]
    arm_r = 0.20 * x
    if name == "fuselage":
        return [0, 0, 0.35 * z], [0, 0, 0], "fuselage_center"
    if name == "arm":
        ang = 45 + i * 90; a = math.radians(ang)
        return [round(arm_r * math.cos(a), 2), round(arm_r * math.sin(a), 2), 0.35 * z], [0, 0, ang], "radial_arm"
    if name in ("motor_pod", "propeller"):
        ang = 45 + i * 90; a = math.radians(ang); r = arm_r + 0.25 * x
        zf = 0.40 * z if name == "motor_pod" else 0.46 * z
        rule = "arm_tip_pod" if name == "motor_pod" else "pod_top_prop"
        return [round(r * math.cos(a), 2), round(r * math.sin(a), 2), zf], [0, 0, 0], rule
    if name == "landing_gear":
        return [0, 0, 0], [0, 0, 0], "under_body"
    if name == "camera_mount":
        return [0, round(0.18 * y, 2), round(0.30 * z - 16, 2)], [0, 0, 0], "nose_under"
    if name == "battery_bay":
        return [0, 0, round(0.30 * z, 2)], [0, 0, 0], "lower_fuselage"
    if name == "controller_deck":
        return [0, 0, round(0.55 * z, 2)], [0, 0, 0], "fuselage_top"
    return [0, 0, 0], [0, 0, 0], "drone_default"


def _generic(graph: dict, env: dict) -> None:
    nodes = graph["nodes"]; n = len(nodes)
    cols = max(1, math.ceil(math.sqrt(n)))
    sx, sy = env["x"] / cols, env["y"] / cols
    for idx, node in enumerate(nodes):
        r, c = divmod(idx, cols)
        px = (c - (cols - 1) / 2) * sx
        py = (r - (cols - 1) / 2) * sy
        node["placement"] = {"translate_mm": [round(px, 2), round(py, 2), 0.0],
                             "rotate_deg": [0, 0, 0], "rule": "generic_grid"}


_DOMAIN = {"quadcopter drone": _drone_xform}


def resolve(graph: dict, design_spec: dict) -> dict:
    kind = design_spec.get("object_kind", "")
    env = graph["envelope_mm"]
    rule = _DOMAIN.get(kind)
    if rule:
        for node in graph["nodes"]:
            t, r, name = rule(node, env)
            node["placement"] = {"translate_mm": [round(v, 2) for v in t],
                                 "rotate_deg": [round(v, 2) for v in r], "rule": name}
        graph["placement_engine"] = f"domain:{kind}"
    else:
        _generic(graph, env)
        graph["placement_engine"] = "generic_layout"
    return graph
