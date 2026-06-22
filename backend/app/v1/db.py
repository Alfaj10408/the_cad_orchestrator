"""SQLite index for the /v1 API (users, api_keys, jobs). WAL; stdlib sqlite3."""
from __future__ import annotations
import sqlite3, uuid
from datetime import datetime, timezone
from app.core import config

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or config.API_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={config.API_DB_BUSY_TIMEOUT_MS}")
    return conn

def get_conn():
    """FastAPI dependency: a fresh per-request connection, closed at teardown."""
    conn = connect()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      id TEXT PRIMARY KEY, name TEXT, is_admin INTEGER DEFAULT 0, created_at TEXT);
    CREATE TABLE IF NOT EXISTS api_keys(
      id TEXT PRIMARY KEY, user_id TEXT, key_hash TEXT UNIQUE, key_prefix TEXT,
      created_at TEXT, revoked_at TEXT);
    CREATE TABLE IF NOT EXISTS jobs(
      job_id TEXT PRIMARY KEY, user_id TEXT, project_id TEXT, status TEXT, stage TEXT,
      failure_class TEXT, created_at TEXT, started_at TEXT, completed_at TEXT,
      queue_pos INTEGER, metrics_json TEXT);
    CREATE TABLE IF NOT EXISTS user_quota(
      user_id TEXT PRIMARY KEY, daily_job_limit INTEGER, max_in_flight INTEGER);
    """)
    conn.commit()

def create_user(conn, name, is_admin=False) -> str:
    uid = uuid.uuid4().hex
    conn.execute("INSERT INTO users(id,name,is_admin,created_at) VALUES(?,?,?,?)",
                 (uid, name, 1 if is_admin else 0, _now())); conn.commit()
    return uid

def create_api_key(conn, user_id, key_hash, key_prefix) -> str:
    kid = uuid.uuid4().hex
    conn.execute("INSERT INTO api_keys(id,user_id,key_hash,key_prefix,created_at) VALUES(?,?,?,?,?)",
                 (kid, user_id, key_hash, key_prefix, _now())); conn.commit()
    return kid

def get_user(conn, user_id):
    return conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()

def get_key_by_hash(conn, key_hash):
    return conn.execute(
        "SELECT * FROM api_keys WHERE key_hash=? AND revoked_at IS NULL", (key_hash,)).fetchone()

def revoke_key(conn, key_id) -> None:
    conn.execute("UPDATE api_keys SET revoked_at=? WHERE id=?", (_now(), key_id)); conn.commit()

def insert_job(conn, job_id, user_id, project_id, status="pending", queue_pos=None) -> None:
    conn.execute("INSERT INTO jobs(job_id,user_id,project_id,status,created_at,queue_pos) "
                 "VALUES(?,?,?,?,?,?)", (job_id, user_id, project_id, status, _now(), queue_pos))
    conn.commit()

def update_job(conn, job_id, **fields) -> None:
    if not fields: return
    cols = ",".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE jobs SET {cols} WHERE job_id=?", (*fields.values(), job_id)); conn.commit()

def get_job_row(conn, job_id):
    return conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()

def list_pending_jobs(conn):
    return conn.execute("SELECT * FROM jobs WHERE status='pending' ORDER BY created_at").fetchall()

def list_running_jobs(conn):
    return conn.execute("SELECT * FROM jobs WHERE status='running'").fetchall()

def pending_position(conn, job_id):
    """1-based rank of job_id among status='pending' rows by (created_at, job_id);
    None if the job is missing or not pending."""
    row = conn.execute(
        "SELECT created_at, status FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    if row is None or row["status"] != "pending":
        return None
    return conn.execute(
        "SELECT COUNT(*) AS c FROM jobs WHERE status='pending' "
        "AND (created_at < ? OR (created_at = ? AND job_id <= ?))",
        (row["created_at"], row["created_at"], job_id)).fetchone()["c"]

def count_in_flight(conn, user_id):
    return conn.execute(
        "SELECT COUNT(*) AS c FROM jobs WHERE user_id=? AND status IN ('pending','running')",
        (user_id,)).fetchone()["c"]

def count_created_since(conn, user_id, since_iso):
    return conn.execute(
        "SELECT COUNT(*) AS c FROM jobs WHERE user_id=? AND created_at >= ?",
        (user_id, since_iso)).fetchone()["c"]

def get_quota(conn, user_id):
    row = conn.execute(
        "SELECT daily_job_limit, max_in_flight FROM user_quota WHERE user_id=?",
        (user_id,)).fetchone()
    daily = row["daily_job_limit"] if row and row["daily_job_limit"] is not None else config.API_DEFAULT_DAILY_JOB_LIMIT
    inflight = row["max_in_flight"] if row and row["max_in_flight"] is not None else config.API_DEFAULT_MAX_IN_FLIGHT
    return daily, inflight

def set_quota(conn, user_id, daily_job_limit, max_in_flight):
    conn.execute(
        "INSERT INTO user_quota(user_id, daily_job_limit, max_in_flight) VALUES(?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET daily_job_limit=excluded.daily_job_limit, "
        "max_in_flight=excluded.max_in_flight",
        (user_id, daily_job_limit, max_in_flight)); conn.commit()

def clear_quota(conn, user_id):
    conn.execute("DELETE FROM user_quota WHERE user_id=?", (user_id,)); conn.commit()

def failure_summary(conn, limit: int = 50):
    """(counts by failure_class, recent failed/cancelled jobs). Read-only."""
    counts = {}
    for r in conn.execute(
            "SELECT COALESCE(failure_class,'none') AS fc, COUNT(*) AS n "
            "FROM jobs WHERE status IN ('failed','cancelled') GROUP BY fc").fetchall():
        counts[r["fc"]] = r["n"]
    recent = [dict(job_id=r["job_id"], status=r["status"],
                   failure_class=r["failure_class"], completed_at=r["completed_at"])
              for r in conn.execute(
                  "SELECT job_id, status, failure_class, completed_at FROM jobs "
                  "WHERE status IN ('failed','cancelled') "
                  "ORDER BY completed_at DESC LIMIT ?", (int(limit),)).fetchall()]
    return counts, recent
