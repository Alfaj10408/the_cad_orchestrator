"""Claude Code CLI adapter (subscription auth, no ANTHROPIC_API_KEY).

Runs the local authenticated `claude` binary in headless stream-json mode
inside a per-job sandbox workspace, parses its event stream, normalizes it to
safe GenEvents, and publishes them to the event bus. Never exposes auth files,
tokens, or ~/.claude contents.

Security:
- asyncio.create_subprocess_exec only (no shell, no string concatenation).
- prompt passed as a single separated argv value (not interpolated).
- cwd is the job workspace, never the repo / backend / /root.
- all Claude-written paths validated against the workspace root.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.core import config
from app.schemas.events import SOURCE_CLAUDE, SOURCE_SYSTEM
from app.services.event_service import JobChannel

# Concurrency gate + running-process registry (for cancellation).
_semaphore: Optional[asyncio.Semaphore] = None
_running: dict[str, asyncio.subprocess.Process] = {}
_cancelled: set[str] = set()


def _sem() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(config.CLAUDE_CODE_MAX_CONCURRENT)
    return _semaphore


# ---------- Workspace ----------
def workspace_dir(project_id: str) -> Path:
    return config.CLAUDE_CODE_WORKSPACE_ROOT / project_id / "claude-workspace"


def ensure_workspace(project_id: str) -> Path:
    ws = workspace_dir(project_id)
    for sub in ("input", "output", "output/logs", "artifacts"):
        (ws / sub).mkdir(parents=True, exist_ok=True)
    return ws


def safe_workspace_path(ws_root: Path, rel: str) -> Optional[Path]:
    """Resolve rel inside ws_root; reject absolute, traversal, symlink escape."""
    if not rel:
        return None
    cand = Path(rel)
    if cand.is_absolute() or ".." in cand.parts:
        return None
    root = ws_root.resolve()
    target = (root / cand).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


# ---------- Health ----------
def health() -> dict:
    """Claude Code install/auth status. No credentials or file paths leaked."""
    binary = config.CLAUDE_CODE_BINARY
    installed = bool(shutil.which(binary) or Path(binary).exists())
    if not config.CLAUDE_CODE_ENABLED:
        return {"enabled": False, "installed": installed, "authenticated": False,
                "mode": "subscription_cli"}
    if not installed:
        return {"enabled": True, "installed": False, "authenticated": False,
                "detail": "Claude Code binary not found", "mode": "subscription_cli"}

    version = None
    authenticated = False
    try:
        import subprocess

        version = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=15
        ).stdout.strip() or None
        auth = subprocess.run(
            [binary, "auth", "status", "--text"],
            capture_output=True, text=True, timeout=20,
        )
        out = (auth.stdout + auth.stderr).lower()
        authenticated = auth.returncode == 0 and (
            "login method" in out or "account" in out or "email" in out
        )
    except Exception as exc:  # noqa: BLE001
        return {"enabled": True, "installed": True, "authenticated": False,
                "detail": f"auth check failed: {type(exc).__name__}",
                "binary": binary, "mode": "subscription_cli"}

    detail = None if authenticated else "Claude Code is installed but not authenticated"
    return {
        "enabled": True,
        "installed": True,
        "authenticated": authenticated,
        "detail": detail,
        "binary": binary,
        "version": version,
        "mode": "subscription_cli",
    }


# ---------- Cancellation ----------
def cancel(job_id: str) -> bool:
    """Request cancellation; kill the child process if running."""
    _cancelled.add(job_id)
    proc = _running.get(job_id)
    if proc and proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return True
    return False


async def shutdown() -> None:
    """Kill any orphaned Claude processes on backend shutdown."""
    for job_id, proc in list(_running.items()):
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
    _running.clear()


# ---------- Stream normalization ----------
def _normalize(obj: dict) -> Optional[dict]:
    """Map one raw stream-json object to a GenEvent kwargs dict, or None.

    Returns None for noise (hooks, status, rate-limit) that should stay only in
    the raw debug log.
    """
    t = obj.get("type")

    if t == "system":
        sub = obj.get("subtype")
        if sub == "init":
            return {
                "source": SOURCE_CLAUDE, "type": "claude.started",
                "stage": "code_generation",
                "message": f"Claude Code started (model {obj.get('model', '?')})",
                "data": {"session_id": obj.get("session_id"),
                         "model": obj.get("model"), "cwd": obj.get("cwd")},
            }
        return None  # hook_started / hook_response / status -> noise

    if t == "stream_event":
        ev = obj.get("event", {}) or {}
        if ev.get("type") == "content_block_delta":
            delta = ev.get("delta", {}) or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                return {"source": SOURCE_CLAUDE, "type": "text.delta",
                        "stage": "code_generation", "delta": delta["text"]}
        return None

    if t in ("text_delta", "content_block_delta"):
        text = obj.get("text") or (obj.get("delta") or {}).get("text")
        if text:
            return {"source": SOURCE_CLAUDE, "type": "text.delta",
                    "stage": "code_generation", "delta": text}
        return None

    if t == "assistant":
        msg = obj.get("message", obj)
        for block in msg.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name", "tool")
                inp = block.get("input", {}) or {}
                fpath = inp.get("file_path") or inp.get("path") or inp.get("notebook_path")
                ftype = "file.created" if name == "Write" else (
                    "file.updated" if name in ("Edit", "MultiEdit", "NotebookEdit") else None)
                if ftype and fpath:
                    return {"source": SOURCE_CLAUDE, "type": ftype,
                            "stage": "code_generation",
                            "message": f"{name}: {Path(str(fpath)).name}",
                            "data": {"tool": name, "path": str(fpath)}}
                return {"source": SOURCE_CLAUDE, "type": "tool.started",
                        "stage": "code_generation",
                        "message": f"Using tool {name}",
                        "data": {"tool": name}}
        return None

    if t == "user":
        msg = obj.get("message", obj)
        for block in msg.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                return {"source": SOURCE_CLAUDE, "type": "tool.completed",
                        "stage": "code_generation", "message": "Tool finished"}
        return None

    return None


async def _drain_stderr(stream, raw_log: Path) -> None:
    try:
        while True:
            line = await stream.readline()
            if not line:
                break
            with raw_log.open("a") as fh:
                fh.write("[stderr] " + line.decode("utf-8", "replace"))
    except Exception:
        pass


async def run_claude(
    project_id: str,
    job_id: str,
    prompt: str,
    channel: JobChannel,
    *,
    model: Optional[str] = None,
    max_turns: Optional[int] = None,
    timeout: Optional[int] = None,
) -> dict:
    """Run Claude Code headless, stream normalized events, return a summary dict.

    Returns {ok, session_id, result_text, exit_code, error}.
    """
    if len(prompt) > config.CLAUDE_CODE_MAX_PROMPT_CHARS:
        prompt = prompt[: config.CLAUDE_CODE_MAX_PROMPT_CHARS]

    ws = ensure_workspace(project_id)
    raw_log = ws / "output" / "logs" / "claude_raw.jsonl"
    binary = config.CLAUDE_CODE_BINARY
    argv = [
        binary, "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--permission-mode", config.CLAUDE_CODE_PERMISSION_MODE,
        "--tools", config.CLAUDE_CODE_TOOLS,
        "--model", model or config.CLAUDE_CODE_MODEL,
        "--max-turns", str(max_turns or config.CLAUDE_CODE_MAX_TURNS),
        prompt,
    ]

    # Do not leak ANTHROPIC_API_KEY semantics; pass a clean-ish env that keeps
    # the user's authenticated ~/.claude (HOME) but drops any stray API key.
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)

    session_id: Optional[str] = None
    result_text: Optional[str] = None
    is_error = False
    total_bytes = 0

    if job_id in _cancelled:
        _cancelled.discard(job_id)

    async with _sem():
        if job_id in _cancelled:
            return {"ok": False, "session_id": None, "result_text": None,
                    "exit_code": None, "error": "cancelled before start"}
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(ws),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                # stream-json emits large single-line messages (file echoes); the
                # default 64KB StreamReader limit overflows on detailed output.
                limit=16 * 1024 * 1024,
            )
        except FileNotFoundError:
            return {"ok": False, "session_id": None, "result_text": None,
                    "exit_code": None, "error": "claude binary not found"}

        _running[job_id] = proc
        stderr_task = asyncio.create_task(_drain_stderr(proc.stderr, raw_log))

        async def _read_stream() -> None:
            nonlocal session_id, result_text, is_error, total_bytes
            while True:
                try:
                    line = await proc.stdout.readline()
                except (ValueError, asyncio.LimitOverrunError):
                    # Pathologically long line beyond the buffer — skip it safely.
                    continue
                if not line:
                    break
                total_bytes += len(line)
                if total_bytes > config.CLAUDE_CODE_MAX_OUTPUT_BYTES:
                    proc.kill()
                    break
                with raw_log.open("a") as fh:
                    fh.write(line.decode("utf-8", "replace"))
                text = line.decode("utf-8", "replace").strip()
                if not text:
                    continue
                try:
                    obj = json.loads(text)
                except json.JSONDecodeError:
                    continue  # malformed line: keep in raw log, do not crash
                if obj.get("type") == "system" and obj.get("subtype") == "init":
                    session_id = obj.get("session_id") or session_id
                if obj.get("type") == "result":
                    is_error = bool(obj.get("is_error"))
                    result_text = obj.get("result")
                    session_id = obj.get("session_id") or session_id
                norm = _normalize(obj)
                if norm:
                    await channel.publish(**norm)

        try:
            await asyncio.wait_for(
                _read_stream(),
                timeout=timeout or config.CLAUDE_CODE_TIMEOUT_SECONDS,
            )
            await proc.wait()
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            return {"ok": False, "session_id": session_id, "result_text": None,
                    "exit_code": proc.returncode, "error": "claude timeout"}
        finally:
            stderr_task.cancel()
            _running.pop(job_id, None)

        if job_id in _cancelled:
            _cancelled.discard(job_id)
            return {"ok": False, "session_id": session_id, "result_text": result_text,
                    "exit_code": proc.returncode, "error": "cancelled"}

        exit_code = proc.returncode
        ok = exit_code == 0 and not is_error
        return {
            "ok": ok,
            "session_id": session_id,
            "result_text": result_text,
            "exit_code": exit_code,
            "error": None if ok else (
                f"claude exit {exit_code}, is_error={is_error}"),
        }


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
