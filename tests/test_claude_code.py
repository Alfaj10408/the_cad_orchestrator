"""Claude Code bridge tests — fully mocked, no Claude credits consumed.

Runnable standalone:  python tests/test_claude_code.py  (also pytest-compatible)
"""
from __future__ import annotations

import asyncio
import os
import stat
import sys
import tempfile
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.core import config  # noqa: E402
from app.services import claude_code_adapter as cca  # noqa: E402
from app.services import event_service  # noqa: E402

_TMP = Path(tempfile.mkdtemp())
config.PROJECTS_ROOT = _TMP / "projects"  # type: ignore
config.CLAUDE_CODE_WORKSPACE_ROOT = _TMP / "runs"  # type: ignore
# Rebind paths module references (it imported PROJECTS_ROOT by value).
from app.core import paths  # noqa: E402
paths.PROJECTS_ROOT = config.PROJECTS_ROOT  # type: ignore


def _write_fake(body: str) -> str:
    f = _TMP / f"fake_claude_{abs(hash(body)) % 99999}.sh"
    f.write_text("#!/usr/bin/env bash\n" + body)
    f.chmod(f.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(f)


SUCCESS_BODY = """
mkdir -p output
cat > output/generate.py <<'EOF'
from build123d import *
W = 10
def gen_step():
    return Box(W, W, W)
EOF
echo '{"type":"system","subtype":"init","session_id":"sess-test","model":"sonnet","cwd":"x"}'
echo 'THIS IS NOT JSON'
echo '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"hello "}}}'
echo '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Write","input":{"file_path":"output/generate.py"}}]}}'
echo '{"type":"result","subtype":"success","is_error":false,"result":"done","session_id":"sess-test"}'
echo "some stderr noise" >&2
exit 0
"""

FAIL_BODY = """
echo '{"type":"system","subtype":"init","session_id":"s2"}'
echo '{"type":"result","subtype":"error","is_error":true,"result":"boom"}'
exit 1
"""

TIMEOUT_BODY = """
echo '{"type":"system","subtype":"init","session_id":"s3"}'
sleep 10
"""


def _channel(pid: str, jid: str):
    return event_service.get_channel(pid, jid)


async def _collect(pid: str, jid: str, fake_body: str, timeout=None):
    config.CLAUDE_CODE_BINARY = _write_fake(fake_body)  # type: ignore
    ch = _channel(pid, jid)
    got: list = []
    orig = ch.publish

    async def spy(*a, **k):
        ev = await orig(*a, **k)
        got.append(ev)
        return ev

    ch.publish = spy  # type: ignore
    res = await cca.run_claude(pid, jid, "make a cube", ch, timeout=timeout)
    return res, got


def test_path_traversal_rejected():
    ws = _TMP / "ws_root"
    ws.mkdir(exist_ok=True)
    assert cca.safe_workspace_path(ws, "../escape") is None
    assert cca.safe_workspace_path(ws, "/etc/passwd") is None
    assert cca.safe_workspace_path(ws, "output/ok.py") is not None


def test_health_binary_unavailable():
    old = config.CLAUDE_CODE_BINARY
    config.CLAUDE_CODE_BINARY = "/nonexistent/claude_xyz"  # type: ignore
    try:
        h = cca.health()
        assert h["installed"] is False and h["authenticated"] is False
    finally:
        config.CLAUDE_CODE_BINARY = old  # type: ignore


def test_success_stream_and_malformed_lines():
    res, got = asyncio.run(_collect("p1", "j1", SUCCESS_BODY))
    assert res["ok"] is True
    assert res["session_id"] == "sess-test"
    types = [e.type for e in got]
    assert "claude.started" in types          # init normalized
    assert "text.delta" in types              # streamed text
    assert "file.created" in types            # Write tool_use
    # malformed line did not crash and produced no event
    deltas = "".join(e.delta or "" for e in got)
    assert "hello" in deltas


def test_nonzero_exit_is_failure():
    res, _ = asyncio.run(_collect("p2", "j2", FAIL_BODY))
    assert res["ok"] is False
    assert res["exit_code"] == 1


def test_timeout():
    res, _ = asyncio.run(_collect("p3", "j3", TIMEOUT_BODY, timeout=1))
    assert res["ok"] is False and res["error"] == "claude timeout"


def test_cancellation():
    async def scenario():
        config.CLAUDE_CODE_BINARY = _write_fake(TIMEOUT_BODY)  # type: ignore
        ch = _channel("p4", "j4")
        task = asyncio.create_task(cca.run_claude("p4", "j4", "x", ch, timeout=30))
        await asyncio.sleep(1.5)  # let it start
        cca.cancel("j4")
        return await task

    res = asyncio.run(scenario())
    assert res["ok"] is False


def test_event_ordering_and_replay():
    async def scenario():
        ch = event_service.get_channel("p5", "j5")
        for i in range(5):
            await ch.publish("system", "text.delta", delta=str(i))
        # ordering
        ids = [e.id for e in ch.replay(0)]
        assert ids == [1, 2, 3, 4, 5]
        # replay honoring last-event-id (ids 1..5 carry deltas "0".."4")
        after = [e.delta for e in ch.replay(3)]
        assert after == ["3", "4"]

    asyncio.run(scenario())


def test_sse_replay_from_jsonl_after_terminal():
    async def scenario():
        ch = event_service.get_channel("p6", "j6")
        await ch.publish("system", "job.started")
        await ch.publish("cad_worker", "artifact.created", message="model.step")
        await ch.publish("system", "job.completed")
        assert ch.terminal is True
        # cold replay path from JSONL
        evs = event_service._read_jsonl("p6", "j6", 0)
        assert [e.type for e in evs][-1] == "job.completed"
        assert event_service.is_terminal_persisted("p6", "j6") is True

    asyncio.run(scenario())


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            import traceback
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
