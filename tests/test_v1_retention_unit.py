import sys, os, sqlite3, time
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest
from app.v1 import retention as rt
from app.core import config as cfg

DAY = 86400


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE jobs(job_id TEXT PRIMARY KEY, user_id TEXT, project_id TEXT,
                 status TEXT, stage TEXT, failure_class TEXT, created_at TEXT,
                 started_at TEXT, completed_at TEXT, queue_pos INTEGER, metrics_json TEXT)""")
    return c


def _job(c, job_id, project_id, status, completed_age_s, now):
    from datetime import datetime, timezone
    completed_at = (None if completed_age_s is None
                    else datetime.fromtimestamp(now - completed_age_s, tz=timezone.utc).isoformat())
    c.execute("INSERT INTO jobs(job_id,user_id,project_id,status,completed_at) VALUES(?,?,?,?,?)",
              (job_id, "u1", project_id, status, completed_at))
    c.commit()


def _mkdir(root, name, age_s=None, now=None):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "out.step").write_text("x" * 100)
    if age_s is not None:
        t = now - age_s
        os.utime(d, (t, t))
    return d


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "PROJECTS_ROOT", tmp_path / "projects")
    (tmp_path / "projects").mkdir()
    monkeypatch.setattr(cfg, "API_RETENTION_COMPLETED_DAYS", 7)
    monkeypatch.setattr(cfg, "API_RETENTION_FAILED_DAYS", 3)
    monkeypatch.setattr(cfg, "API_RETENTION_CANCELLED_DAYS", 1)
    monkeypatch.setattr(cfg, "API_RETENTION_MIN_AGE_S", 3600)
    monkeypatch.setattr(cfg, "API_RETENTION_MAX_DELETE", 1000)
    return cfg.PROJECTS_ROOT


def test_completed_over_window_eligible_and_deleted(env, monkeypatch):
    now = 1_000_000.0
    c = _conn()
    _job(c, "j1", "p1", "completed", 8 * DAY, now)        # > 7d -> eligible
    _mkdir(env, "p1")
    stats = rt.sweep(c, dry_run=False, now=now)
    assert stats.deleted == 1 and stats.by_status["completed"] == 1
    assert not (env / "p1").exists()
    assert stats.reclaimed_bytes >= 100
    # row preserved
    assert c.execute("SELECT 1 FROM jobs WHERE job_id='j1'").fetchone() is not None


def test_completed_under_window_preserved(env):
    now = 1_000_000.0
    c = _conn()
    _job(c, "j1", "p1", "completed", 2 * DAY, now)        # < 7d
    _mkdir(env, "p1")
    stats = rt.sweep(c, dry_run=False, now=now)
    assert stats.deleted == 0 and (env / "p1").exists()


def test_failed_and_cancelled_windows(env):
    now = 1_000_000.0
    c = _conn()
    _job(c, "jf", "pf", "failed", 4 * DAY, now)           # > 3d -> eligible
    _job(c, "jc", "pc", "cancelled", 2 * DAY, now)        # > 1d -> eligible
    _job(c, "jf2", "pf2", "failed", 1 * DAY, now)         # < 3d -> keep
    for n in ("pf", "pc", "pf2"):
        _mkdir(env, n)
    stats = rt.sweep(c, dry_run=False, now=now)
    assert not (env / "pf").exists() and not (env / "pc").exists()
    assert (env / "pf2").exists()
    assert stats.by_status["failed"] == 1 and stats.by_status["cancelled"] == 1


def test_active_never_eligible(env):
    now = 1_000_000.0
    c = _conn()
    _job(c, "jp", "pp", "pending", None, now)
    _job(c, "jr", "pr", "running", None, now)
    _mkdir(env, "pp"); _mkdir(env, "pr")
    stats = rt.sweep(c, dry_run=False, now=now)
    assert stats.deleted == 0 and (env / "pp").exists() and (env / "pr").exists()


def test_min_age_floor_blocks_even_with_override(env):
    now = 1_000_000.0
    c = _conn()
    _job(c, "j1", "p1", "completed", 1800, now)           # 30 min old
    _mkdir(env, "p1")
    # override completed window to 0 days, but floor 3600s must still protect it
    stats = rt.sweep(c, dry_run=False, overrides={"completed": 0}, now=now)
    assert stats.deleted == 0 and (env / "p1").exists()


def test_orphan_dir_eligible_by_mtime(env):
    now = 1_000_000.0
    c = _conn()
    # no job row references 'orphan'
    _mkdir(env, "orphan", age_s=2 * DAY, now=now)
    _mkdir(env, "fresh_orphan", age_s=600, now=now)       # < min age -> keep
    stats = rt.sweep(c, dry_run=False, now=now)
    assert not (env / "orphan").exists() and (env / "fresh_orphan").exists()
    assert stats.by_status["orphan"] == 1


def test_dry_run_reports_but_deletes_nothing(env):
    now = 1_000_000.0
    c = _conn()
    _job(c, "j1", "p1", "completed", 8 * DAY, now)
    _mkdir(env, "p1")
    stats = rt.sweep(c, dry_run=True, now=now)
    assert stats.dry_run is True and stats.eligible == 1 and stats.deleted == 0
    assert stats.reclaimed_bytes >= 100 and (env / "p1").exists()


def test_max_delete_cap(env, monkeypatch):
    now = 1_000_000.0
    monkeypatch.setattr(cfg, "API_RETENTION_MAX_DELETE", 2)
    c = _conn()
    for i in range(5):
        _job(c, f"j{i}", f"p{i}", "completed", 8 * DAY, now)
        _mkdir(env, f"p{i}")
    stats = rt.sweep(c, dry_run=False, now=now)
    assert stats.deleted == 2 and stats.capped is True
    remaining = sum((env / f"p{i}").exists() for i in range(5))
    assert remaining == 3


def test_sweep_logs_metrics(env, caplog):
    import logging as _lg
    now = 1_000_000.0
    c = _conn()
    _job(c, "j1", "p1", "completed", 8 * DAY, now)
    _mkdir(env, "p1")
    with caplog.at_level(_lg.INFO, logger="app.v1.retention"):
        stats = rt.sweep(c, dry_run=False, now=now)
    assert stats.duration_ms >= 0
    assert any("retention sweep" in r.message for r in caplog.records)
