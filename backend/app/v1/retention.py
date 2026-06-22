"""Artifact retention sweep for the /v1 surface (P2).

DB jobs table is the source of truth; project directories under
config.PROJECTS_ROOT are deleted, job rows are preserved. Single-instance.
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.core import config

DAY = 86400
_TERMINAL = ("completed", "failed", "cancelled")

_log = logging.getLogger("app.v1.retention")


@dataclass
class SweepStats:
    dry_run: bool
    scanned: int = 0
    eligible: int = 0
    deleted: int = 0
    reclaimed_bytes: int = 0
    capped: bool = False
    by_status: dict = field(default_factory=lambda: {
        "completed": 0, "failed": 0, "cancelled": 0, "orphan": 0})
    duration_ms: int = 0


def _window_days(status: str, overrides: dict | None) -> int:
    if overrides and status in overrides:
        return int(overrides[status])
    return {
        "completed": config.API_RETENTION_COMPLETED_DAYS,
        "failed": config.API_RETENTION_FAILED_DAYS,
        "cancelled": config.API_RETENTION_CANCELLED_DAYS,
    }[status]


def _completed_epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _safe_under_root(path: Path, root: Path) -> bool:
    """True iff path is a direct child of root (resolved), not root itself."""
    try:
        rp = path.resolve()
    except OSError:
        return False
    return rp.parent == root.resolve() and rp != root.resolve()


def sweep(conn, *, dry_run: bool = True, overrides: dict | None = None,
          now: float | None = None) -> SweepStats:
    start = time.monotonic()
    t = time.time() if now is None else now
    root = config.PROJECTS_ROOT
    floor = config.API_RETENTION_MIN_AGE_S
    cap = config.API_RETENTION_MAX_DELETE
    stats = SweepStats(dry_run=dry_run)
    if not root.exists():
        return stats

    rows = conn.execute(
        "SELECT job_id, project_id, status, completed_at FROM jobs").fetchall()
    known_pids = {r["project_id"] for r in rows if r["project_id"]}
    targets: list[tuple[Path, str]] = []   # (dir, by_status key)

    # 1. terminal jobs past their window + floor
    for r in rows:
        status = r["status"]
        if status not in _TERMINAL or not r["project_id"]:
            continue
        ce = _completed_epoch(r["completed_at"])
        if ce is None:
            continue
        age = t - ce
        window_s = max(_window_days(status, overrides) * DAY, floor)
        if age < window_s:
            continue
        d = root / r["project_id"]
        if d.is_dir() and _safe_under_root(d, root):
            targets.append((d, status))

    # 2. orphan dirs (no job references the name) older than floor
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name in known_pids:
            continue
        try:
            age = t - os.path.getmtime(child)
        except OSError:
            continue
        if age >= floor and _safe_under_root(child, root):
            targets.append((child, "orphan"))

    stats.scanned = len(rows) + sum(1 for c in root.iterdir() if c.is_dir())
    stats.eligible = len(targets)

    for d, key in targets:
        if stats.deleted >= cap:
            stats.capped = True
            break
        size = _dir_size(d)
        if not dry_run:
            try:
                shutil.rmtree(d)
            except OSError:
                continue
        stats.deleted += 1
        stats.reclaimed_bytes += size
        stats.by_status[key] = stats.by_status.get(key, 0) + 1

    if dry_run:
        # report would-delete counts without having deleted
        stats.deleted = 0

    stats.duration_ms = int((time.monotonic() - start) * 1000)
    _log.info(
        "retention sweep: scanned=%d eligible=%d deleted=%d "
        "reclaimed_bytes=%d duration_ms=%d dry_run=%s",
        stats.scanned, stats.eligible, stats.deleted,
        stats.reclaimed_bytes, stats.duration_ms, stats.dry_run,
    )
    return stats
