"""Startup-only reaper for orphaned headless Claude processes (F7).

Stateless: discovers via psutil at boot, no PID table. A process is an orphan
iff it matches the headless-Claude triple-signature (binary + '-p
--output-format stream-json' + cwd under the job workspace root). Run BEFORE
the worker starts, so no live job exists and every match is a prior-instance
orphan. Never kills by binary name alone — the cwd clause guards interactive
dev sessions.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import psutil

from app.core import config

_log = logging.getLogger("app.v1.reaper")


@dataclass
class ReapStats:
    dry_run: bool = False
    scanned: int = 0
    matched: int = 0
    killed: int = 0
    errors: int = 0


def _has_headless_sig(cmdline: list[str]) -> bool:
    if "-p" not in cmdline:
        return False
    for i in range(len(cmdline) - 1):
        if cmdline[i] == "--output-format" and cmdline[i + 1] == "stream-json":
            return True
    return False


def _cwd_under_workspace(cwd: str) -> bool:
    try:
        root = config.CLAUDE_CODE_WORKSPACE_ROOT.resolve()
        return root == Path(cwd).resolve() or root in Path(cwd).resolve().parents
    except (OSError, ValueError):
        return False


def is_orphan_claude(proc) -> bool:
    """Triple-match: binary + headless signature + cwd under workspace root."""
    try:
        cmdline = proc.cmdline()
        if not cmdline:
            return False
        exe = ""
        try:
            exe = proc.exe()
        except (psutil.AccessDenied, psutil.ZombieProcess):
            exe = ""
        binary = config.CLAUDE_CODE_BINARY
        if exe != binary and cmdline[0] != binary:
            return False
        if not _has_headless_sig(cmdline):
            return False
        return _cwd_under_workspace(proc.cwd())
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


def _descendant_pids() -> set[int]:
    try:
        me = psutil.Process(os.getpid())
        return {c.pid for c in me.children(recursive=True)}
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return set()


def reap_orphan_claude(*, dry_run: bool = False) -> ReapStats:
    stats = ReapStats(dry_run=dry_run)
    if not config.API_REAP_ORPHAN_CLAUDE:
        return stats
    skip = {os.getpid()} | _descendant_pids()
    now = time.time()
    for proc in psutil.process_iter():
        stats.scanned += 1
        try:
            if proc.pid in skip:
                continue
            if not is_orphan_claude(proc):
                continue
            stats.matched += 1
            try:
                cwd = proc.cwd()
                ct = proc.create_time()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                cwd, ct = "?", now
            ct_iso = datetime.fromtimestamp(ct, tz=timezone.utc).isoformat()
            runtime = int(now - ct)
            marker = "would-reap" if dry_run else "reaping"
            _log.info("%s orphan claude: pid=%s cwd=%s create_time=%s runtime_seconds=%s",
                      marker, proc.pid, cwd, ct_iso, runtime)
            if not dry_run:
                proc.kill()
                try:
                    proc.wait(timeout=3)
                except (psutil.TimeoutExpired, psutil.NoSuchProcess):
                    pass
                stats.killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            stats.errors += 1
            continue
    return stats
