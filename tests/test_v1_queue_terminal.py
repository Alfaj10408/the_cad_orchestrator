# tests/test_v1_queue_terminal.py
import sys, sqlite3, json
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest
from app.v1 import queue as qmod, db
from app.core import config as cfg


def _conn():
    c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE jobs(job_id TEXT PRIMARY KEY, user_id TEXT, project_id TEXT,
                 status TEXT, stage TEXT, failure_class TEXT, created_at TEXT,
                 started_at TEXT, completed_at TEXT, queue_pos INTEGER, metrics_json TEXT)""")
    c.execute("INSERT INTO jobs(job_id,user_id,project_id,status,created_at) "
              "VALUES('j1','u1','p1','running','2020-01-01T00:00:00+00:00')")
    c.commit(); return c


@pytest.fixture
def metrics(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "PROJECTS_ROOT", tmp_path / "projects")
    reports = tmp_path / "projects" / "p1" / "reports"
    reports.mkdir(parents=True)
    (reports / "component_metrics.json").write_text('{"parts": 3}')
    return tmp_path


def test_terminal_failed_captures_metrics_and_class(metrics):
    c = _conn()
    qmod._terminal(c, "j1", "p1", "failed", failure_class="cad")
    row = c.execute("SELECT status, failure_class, metrics_json, completed_at "
                    "FROM jobs WHERE job_id='j1'").fetchone()
    assert row["status"] == "failed" and row["failure_class"] == "cad"
    assert json.loads(row["metrics_json"])["parts"] == 3
    assert row["completed_at"] is not None


def test_terminal_cancelled_captures_metrics(metrics):
    c = _conn()
    qmod._terminal(c, "j1", "p1", "cancelled")
    row = c.execute("SELECT status, metrics_json FROM jobs WHERE job_id='j1'").fetchone()
    assert row["status"] == "cancelled"
    assert json.loads(row["metrics_json"])["parts"] == 3


def test_terminal_no_metrics_file_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "PROJECTS_ROOT", tmp_path / "projects")
    (tmp_path / "projects" / "p1").mkdir(parents=True)   # no reports/component_metrics.json
    c = _conn()
    qmod._terminal(c, "j1", "p1", "failed", failure_class="internal")
    row = c.execute("SELECT status, failure_class, metrics_json FROM jobs WHERE job_id='j1'").fetchone()
    assert row["status"] == "failed" and row["failure_class"] == "internal"
    assert row["metrics_json"] is None     # _load_metrics returns None -> stored NULL
