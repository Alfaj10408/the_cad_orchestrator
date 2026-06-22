import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))
from app.v1 import db
from app.core import config as cfg

def test_connect_sets_busy_timeout(tmp_path):
    c = db.connect(str(tmp_path / "b.db"))
    bt = c.execute("PRAGMA busy_timeout").fetchone()[0]
    assert bt == cfg.API_DB_BUSY_TIMEOUT_MS == 5000
    c.close()

def test_get_conn_yields_and_closes(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "API_DB_PATH", str(tmp_path / "g.db"))
    gen = db.get_conn()
    conn = next(gen)
    db.init_db(conn)
    db.create_user(conn, "x")          # usable
    try: next(gen)
    except StopIteration: pass         # generator finalizes (closes)
    import pytest
    with pytest.raises(Exception):     # closed connection rejects use
        conn.execute("SELECT 1")
