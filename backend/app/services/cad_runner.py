"""Deterministic MVP v1 CAD generator + STEP/mesh/inspect/snapshot runners.

No LLM. Parses dimensions from the brief, emits a build123d source file
defining gen_step(), then invokes the text-to-cad CLI tools.

Modular helpers:
    generate_step    brief -> source/model.py + cad/model.step
    export_meshes    cad/model.step -> cad/model.stl + cad/model.glb
    inspect_step     cad/model.step -> reports/inspection.txt
    generate_snapshot cad/model.step -> cad/snapshot.png
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess

from app.core.config import PRODUCT_ROOT
from app.core import paths

CAD_PYTHON = "/root/anaconda3/envs/cadskills/bin/python"
LD_PRELOAD = "/root/anaconda3/envs/cadskills/lib/libexpat.so.1"
_TOOLS = PRODUCT_ROOT / "repo" / "text-to-cad" / "skills" / "cad" / "scripts"
STEP_TOOL = _TOOLS / "step"
INSPECT_TOOL = _TOOLS / "inspect"
SNAPSHOT_TOOL = _TOOLS / "snapshot"

# Relative (to project root) artifact paths.
SRC_REL = "source/model.py"
STEP_REL = "cad/model.step"
STL_REL = "cad/model.stl"
GLB_REL = "cad/model.glb"
SNAPSHOT_REL = "cad/snapshot.png"

_DEFAULT = (50.0, 50.0, 50.0)
_DIMS_RE = re.compile(r"(\d+(?:\.\d+)?)")
_SNAP_RE = re.compile(r"saved snapshot:\s*(\S+)")


def _run(tool, args: list[str], cwd) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["LD_PRELOAD"] = LD_PRELOAD
    return subprocess.run(
        [CAD_PYTHON, str(tool), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd),
        timeout=300,
    )


def parse_dimensions(brief: dict) -> tuple[float, float, float]:
    """Pull (w, d, h) in mm from brief; fall back to defaults."""
    params = brief.get("parameters", {}) or {}
    raw = params.get("dimensions") or brief.get("user_answers", {}).get("dimensions")
    if raw:
        nums = [float(n) for n in _DIMS_RE.findall(str(raw))]
        if len(nums) >= 3:
            return nums[0], nums[1], nums[2]
        if len(nums) == 1:
            return nums[0], nums[0], nums[0]
    return _DEFAULT


def build_model_source(prompt: str, dims: tuple[float, float, float]) -> str:
    w, d, h = dims
    is_bracket = "bracket" in (prompt or "").lower()

    header = (
        "# Auto-generated MVP v1 CAD source (deterministic, no LLM).\n"
        "# Origin: part center. XY base plane, +Z up. Units: mm.\n"
        "from build123d import *\n\n"
        f"width = {w}\n"
        f"depth = {d}\n"
        f"height = {h}\n"
    )

    if is_bracket:
        body = (
            "thickness = max(min(width, depth, height) * 0.2, 3.0)\n\n"
            "def gen_step():\n"
            "    # Simple L-bracket: horizontal base + vertical flange.\n"
            "    base = Box(width, depth, thickness)\n"
            "    base.position = (0, 0, thickness / 2)\n"
            "    flange = Box(width, thickness, height)\n"
            "    flange.position = (0, -depth / 2 + thickness / 2, height / 2)\n"
            "    return base + flange\n"
        )
    else:
        body = (
            "\ndef gen_step():\n"
            "    # Simple rectangular block.\n"
            "    return Box(width, depth, height)\n"
        )

    return header + body


def _write_source(project_id: str, code: str) -> None:
    root = paths.project_dir(project_id)
    (root / "source").mkdir(parents=True, exist_ok=True)
    (root / "cad").mkdir(parents=True, exist_ok=True)
    (root / SRC_REL).write_text(code)


def generate_source_from_template(project_id: str, brief: dict) -> dict:
    """Deterministic source: write source/model.py from the brief template."""
    prompt = brief.get("prompt", "")
    dims = parse_dimensions(brief)
    _write_source(project_id, build_model_source(prompt, dims))
    return {
        "source": SRC_REL,
        "dimensions": list(dims),
        "is_bracket": "bracket" in prompt.lower(),
        "mode": "deterministic",
    }


def generate_source_from_llm(project_id: str, code: str) -> dict:
    """LLM source: write already-safety-checked code to source/model.py."""
    _write_source(project_id, code)
    return {"source": SRC_REL, "mode": "llm"}


def export_step(project_id: str) -> dict:
    """Run the step tool on the existing source/model.py -> cad/model.step."""
    root = paths.project_dir(project_id)
    proc = _run(STEP_TOOL, [f"{SRC_REL}={STEP_REL}"], cwd=root)
    ok = proc.returncode == 0 and (root / STEP_REL).exists()
    return {
        "ok": ok,
        "stderr": proc.stderr,
        "source": SRC_REL,
        "step": STEP_REL if ok else None,
    }


def generate_step(project_id: str, brief: dict) -> dict:
    """Deterministic convenience: template source + STEP export."""
    src = generate_source_from_template(project_id, brief)
    step = export_step(project_id)
    return {**src, **step}


def export_meshes(project_id: str) -> dict:
    """Export STL + GLB sidecars from cad/model.step source."""
    root = paths.project_dir(project_id)
    # Sidecar --stl/--glb paths are resolved relative to the STEP output
    # directory (cad/), so pass basenames to land them in cad/.
    proc = _run(
        STEP_TOOL,
        [f"{SRC_REL}={STEP_REL}", "--stl", "model.stl", "--glb", "model.glb", "--force"],
        cwd=root,
    )

    # Fallback: copy hidden .model.step.glb if --glb produced nothing.
    glb_path = root / GLB_REL
    if not glb_path.exists():
        hidden = root / "cad" / ".model.step.glb"
        if hidden.exists():
            shutil.copyfile(hidden, glb_path)

    stl_ok = (root / STL_REL).exists()
    glb_ok = glb_path.exists()
    ok = proc.returncode == 0 and stl_ok and glb_ok
    return {
        "ok": ok,
        "stderr": proc.stderr,
        "stl": STL_REL if stl_ok else None,
        "glb": GLB_REL if glb_ok else None,
    }


def inspect_step(project_id: str) -> dict:
    """Run inspect refs on the STEP and save reports/inspection.txt."""
    root = paths.project_dir(project_id)
    proc = _run(INSPECT_TOOL, ["refs", f"@cad[{STEP_REL}]"], cwd=root)

    report_rel = "reports/inspection.txt"
    (root / "reports").mkdir(parents=True, exist_ok=True)
    content = proc.stdout or ""
    if proc.stderr:
        content += "\n--- stderr ---\n" + proc.stderr
    (root / report_rel).write_text(content)

    ok = proc.returncode == 0
    return {"ok": ok, "stderr": proc.stderr, "report": report_rel}


def generate_snapshot(project_id: str) -> dict:
    """Render a PNG snapshot of the STEP into cad/snapshot.png."""
    root = paths.project_dir(project_id)
    proc = _run(
        SNAPSHOT_TOOL, ["--input", STEP_REL, "--output", SNAPSHOT_REL], cwd=root
    )

    # Tool inserts a UTC timestamp before the extension; normalize to fixed name.
    produced = None
    m = _SNAP_RE.search(proc.stdout or "")
    if m:
        produced = m.group(1)
    if produced:
        p = (root / produced) if not os.path.isabs(produced) else None
        src = p if (p and p.exists()) else (produced if os.path.exists(produced) else None)
        if src:
            shutil.copyfile(src, root / SNAPSHOT_REL)

    if not (root / SNAPSHOT_REL).exists():
        # Fallback: newest cad/snapshot*.png.
        candidates = sorted((root / "cad").glob("snapshot*.png"))
        if candidates:
            shutil.copyfile(candidates[-1], root / SNAPSHOT_REL)

    ok = proc.returncode == 0 and (root / SNAPSHOT_REL).exists()
    return {
        "ok": ok,
        "stderr": proc.stderr,
        "snapshot": SNAPSHOT_REL if (root / SNAPSHOT_REL).exists() else None,
    }
