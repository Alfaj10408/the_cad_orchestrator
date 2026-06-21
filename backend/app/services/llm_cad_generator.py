"""LLM-driven build123d source generation (optional MVP v1 mode).

Uses the Anthropic Python SDK to produce a gen_step() build123d source from
the engineering brief. The generated code is safety-checked before it is ever
written to disk or executed by the CAD tool path.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from app.core.config import PRODUCT_ROOT

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "anthropic")
LLM_MODEL = os.environ.get("LLM_MODEL", "claude-opus-4-8")
_SYSTEM_PATH = (
    PRODUCT_ROOT / "backend" / "app" / "ai" / "prompts" / "llm_cad_system.txt"
)

# Substrings rejected anywhere in generated code before execution.
BANNED = (
    "import os",
    "import subprocess",
    "import socket",
    "open(",
    "eval(",
    "exec(",
    "requests",
    "pathlib",
    "shutil",
    "__import__",
)

_FENCE_RE = re.compile(r"```(?:python)?\s*(.*?)```", re.DOTALL)


class LLMNotConfigured(RuntimeError):
    pass


class UnsafeGeneratedCode(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def check_code_safety(code: str) -> tuple[bool, str | None]:
    """Reject code containing forbidden imports / IO / dynamic execution."""
    lowered = code
    for token in BANNED:
        if token in lowered:
            return False, f"forbidden token in generated code: {token!r}"
    if "def gen_step" not in code:
        return False, "generated code does not define gen_step()"
    return True, None


def _extract_code(text: str) -> str:
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    return text.strip()


def _system_prompt() -> str:
    return _SYSTEM_PATH.read_text()


def _brief_user_prompt(brief: dict) -> str:
    params = brief.get("parameters", {}) or {}
    answers = brief.get("user_answers", {}) or {}
    dims = params.get("dimensions") or answers.get("dimensions") or "unspecified"
    material = params.get("material") or answers.get("material") or "unspecified"
    return (
        "Engineering brief:\n"
        f"- Prompt: {brief.get('prompt', '')}\n"
        f"- Intent: {brief.get('intent', '')}\n"
        f"- Dimensions: {dims}\n"
        f"- Material: {material}\n"
        "\nGenerate the build123d source now."
    )


def _call_llm(system: str, user: str) -> str:
    if not is_configured():
        raise LLMNotConfigured(
            "LLM mode requested but ANTHROPIC_API_KEY is not configured"
        )
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise LLMNotConfigured(
            "anthropic SDK not installed; run pip install anthropic"
        ) from exc

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=LLM_MODEL,
        max_tokens=8000,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
    return _extract_code("\n".join(parts))


def generate_source(brief: dict, user_prompt: str | None = None) -> str:
    """Return safety-checked build123d source for the brief.

    `user_prompt` overrides the default brief-derived message (used to inject
    an orchestrator-built worker prompt); falls back to _brief_user_prompt.
    """
    user = user_prompt or _brief_user_prompt(brief)
    code = _call_llm(_system_prompt(), user)
    ok, reason = check_code_safety(code)
    if not ok:
        raise UnsafeGeneratedCode(reason or "unsafe generated code")
    return code


def repair_source(
    brief: dict, previous_code: str, error: str, user_prompt: str | None = None
) -> str:
    """One repair attempt using the prior source and the failure message."""
    base = user_prompt or _brief_user_prompt(brief)
    user = (
        base
        + "\n\nThe previous attempt failed. Previous source:\n"
        + previous_code
        + "\n\nError:\n"
        + error
        + "\n\nReturn corrected build123d source only."
    )
    code = _call_llm(_system_prompt(), user)
    ok, reason = check_code_safety(code)
    if not ok:
        raise UnsafeGeneratedCode(reason or "unsafe generated code")
    return code
