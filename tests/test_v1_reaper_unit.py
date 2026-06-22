# tests/test_v1_reaper_unit.py
import sys, os, time, logging
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest
from app.v1 import reaper as rp
from app.core import config as cfg


class FakeProc:
    def __init__(self, pid, exe, cmdline, cwd, create_time=0.0,
                 raise_on=None):
        self.pid = pid
        self._exe = exe
        self._cmdline = cmdline
        self._cwd = cwd
        self._create_time = create_time
        self._raise_on = raise_on or set()
        self.killed = False
        self.waited = False
    def exe(self):
        if "exe" in self._raise_on: raise rp.psutil.AccessDenied(self.pid)
        return self._exe
    def cmdline(self):
        if "cmdline" in self._raise_on: raise rp.psutil.AccessDenied(self.pid)
        return self._cmdline
    def cwd(self):
        if "cwd" in self._raise_on: raise rp.psutil.AccessDenied(self.pid)
        return self._cwd
    def create_time(self): return self._create_time
    def kill(self):
        if "kill" in self._raise_on: raise rp.psutil.NoSuchProcess(self.pid)
        self.killed = True
    def wait(self, timeout=None): self.waited = True


@pytest.fixture
def env(tmp_path, monkeypatch):
    ws = tmp_path / "runs"; ws.mkdir()
    monkeypatch.setattr(cfg, "CLAUDE_CODE_BINARY", "/root/.local/bin/claude")
    monkeypatch.setattr(cfg, "CLAUDE_CODE_WORKSPACE_ROOT", ws)
    monkeypatch.setattr(cfg, "API_REAP_ORPHAN_CLAUDE", True)
    return ws


def _orphan(env, pid=4242):
    wsdir = env / "p1" / "claude-workspace"; wsdir.mkdir(parents=True, exist_ok=True)
    return FakeProc(pid, "/root/.local/bin/claude",
                    ["/root/.local/bin/claude", "-p", "--output-format", "stream-json",
                     "--verbose", "the-prompt"], str(wsdir), create_time=1000.0)


def _patch_iter(monkeypatch, procs):
    monkeypatch.setattr(rp.psutil, "process_iter", lambda *a, **k: list(procs))
    monkeypatch.setattr(rp, "_descendant_pids", lambda: set())


def test_is_orphan_triple_match(env):
    assert rp.is_orphan_claude(_orphan(env)) is True


def test_not_orphan_binary_only(env):
    # right binary, but NO headless stream-json signature -> interactive session
    p = FakeProc(10, "/root/.local/bin/claude",
                 ["/root/.local/bin/claude"], str(env / "p1" / "claude-workspace"))
    (env / "p1" / "claude-workspace").mkdir(parents=True, exist_ok=True)
    assert rp.is_orphan_claude(p) is False


def test_not_orphan_cwd_outside_workspace(env, tmp_path):
    outside = tmp_path / "elsewhere"; outside.mkdir()
    p = FakeProc(11, "/root/.local/bin/claude",
                 ["/root/.local/bin/claude", "-p", "--output-format", "stream-json"],
                 str(outside))
    assert rp.is_orphan_claude(p) is False


def test_not_orphan_wrong_binary(env):
    p = FakeProc(12, "/usr/bin/python",
                 ["/usr/bin/python", "-p", "--output-format", "stream-json"],
                 str(env / "p1" / "claude-workspace"))
    (env / "p1" / "claude-workspace").mkdir(parents=True, exist_ok=True)
    assert rp.is_orphan_claude(p) is False


def test_reap_kills_orphan(env, monkeypatch):
    p = _orphan(env)
    _patch_iter(monkeypatch, [p])
    stats = rp.reap_orphan_claude()
    assert stats.killed == 1 and stats.matched == 1 and p.killed is True


def test_reap_dry_run_lists_not_kills(env, monkeypatch):
    p = _orphan(env)
    _patch_iter(monkeypatch, [p])
    stats = rp.reap_orphan_claude(dry_run=True)
    assert stats.matched == 1 and stats.killed == 0 and p.killed is False


def test_reap_skips_self_and_descendants(env, monkeypatch):
    p = _orphan(env, pid=os.getpid())     # own pid must be skipped
    monkeypatch.setattr(rp.psutil, "process_iter", lambda *a, **k: [p])
    monkeypatch.setattr(rp, "_descendant_pids", lambda: set())
    stats = rp.reap_orphan_claude()
    assert stats.killed == 0 and p.killed is False


def test_reap_swallows_psutil_errors(env, monkeypatch):
    bad = _orphan(env); bad._raise_on = {"cwd"}     # AccessDenied during match
    _patch_iter(monkeypatch, [bad])
    stats = rp.reap_orphan_claude()                 # must not raise
    assert stats.killed == 0


def test_reap_disabled_noop(env, monkeypatch):
    monkeypatch.setattr(cfg, "API_REAP_ORPHAN_CLAUDE", False)
    p = _orphan(env)
    _patch_iter(monkeypatch, [p])
    stats = rp.reap_orphan_claude()
    assert stats.scanned == 0 and stats.killed == 0 and p.killed is False


def test_reap_logs_pid_cwd_createtime_runtime(env, monkeypatch, caplog):
    p = _orphan(env)
    _patch_iter(monkeypatch, [p])
    with caplog.at_level(logging.INFO, logger="app.v1.reaper"):
        rp.reap_orphan_claude()
    blob = " ".join(r.getMessage() for r in caplog.records)
    assert "pid=" in blob and "cwd=" in blob
    assert "create_time=" in blob and "runtime_seconds=" in blob
