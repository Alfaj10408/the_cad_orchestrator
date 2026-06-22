"""Concurrent-writer regression test for the /v1 SQLite per-request connection model.

Fires 20 parallel POST /v1/jobs (each writes: insert_job + update_job) plus
interleaved GET /v1/jobs/{id} and GET /v1/me — all via ThreadPoolExecutor —
and asserts zero 500s and zero "database is locked" errors.

This is the F5 Task 6 regression: WAL + per-request connections must allow all
20 concurrent writers to commit without a "database is locked" failure.
"""
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import config as _config
from app.v1 import auth, db, routes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_app(tmp_path, monkeypatch):
    db_path = str(tmp_path / "conc.db")

    # Patch config so db.connect() (which reads config.API_DB_PATH at call
    # time) and auth/routes see the tmp path.
    monkeypatch.setattr(_config, "API_DB_PATH", db_path)
    # Disable quota for this test (concurrency test, not quota test).
    # Routes read app.core.config.API_QUOTA_ENABLED at request time.
    monkeypatch.setattr(_config, "API_QUOTA_ENABLED", False)
    import app.v1.db as _db_mod
    monkeypatch.setattr(_db_mod.config, "API_DB_PATH", db_path)

    # Init schema once
    init_conn = db.connect(db_path)
    db.init_db(init_conn)
    init_conn.close()

    # Fake queue: enqueue is a no-op (just bumps a counter), depth=0.
    # This isolates the DB-write path of POST /v1/jobs from the real pipeline.
    class _FakeQueue:
        def __init__(self):
            self._lock = threading.Lock()
            self._count = 0

        def enqueue(self, jid):
            with self._lock:
                self._count += 1
                return self._count

        def depth(self):
            return 0

        def alive(self):
            return True

    app = FastAPI()
    app.state.queue = _FakeQueue()
    app.include_router(routes.router)
    return app, db_path


# ---------------------------------------------------------------------------
# The regression test
# ---------------------------------------------------------------------------

N_WORKERS = 20


def test_concurrent_writers_no_locked(tmp_path, monkeypatch):
    """20 concurrent POST /v1/jobs must all return 201; no 500s; no 'database is locked'."""
    app, db_path = _build_app(tmp_path, monkeypatch)

    # Mint a single API key used by all concurrent requests
    mint_conn = db.connect(db_path)
    key, _, _, uid = auth.mint_key(mint_conn, "load_user")
    mint_conn.close()

    headers = {"Authorization": f"Bearer {key}"}
    client = TestClient(app, raise_server_exceptions=False)

    # -----------------------------------------------------------------------
    # Phase 1: fire N_WORKERS concurrent POST /v1/jobs
    # -----------------------------------------------------------------------
    post_results = []
    post_lock = threading.Lock()

    def do_post(i):
        r = client.post(
            "/v1/jobs",
            json={"prompt": f"a {i}x10x5 block"},
            headers=headers,
        )
        return r.status_code, r.text

    with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
        futures = {ex.submit(do_post, i): i for i in range(N_WORKERS)}
        for fut in as_completed(futures):
            status, body = fut.result()
            post_results.append((status, body))

    # Collect the job_ids that were successfully created
    import json as _json
    created_job_ids = []
    for status, body in post_results:
        if status == 201:
            try:
                created_job_ids.append(_json.loads(body)["job_id"])
            except Exception:
                pass

    # Assert: every POST returned 201
    post_statuses = [s for s, _ in post_results]
    locked_bodies = [b for s, b in post_results if "database is locked" in b.lower()]

    assert not locked_bodies, (
        f"'database is locked' appeared in {len(locked_bodies)} POST response(s): "
        f"{locked_bodies[:3]}"
    )
    assert all(s == 201 for s in post_statuses), (
        f"Expected all 201, got: {sorted(set(post_statuses))}"
    )
    assert len(created_job_ids) == N_WORKERS, (
        f"Expected {N_WORKERS} job_ids, got {len(created_job_ids)}"
    )

    # -----------------------------------------------------------------------
    # Phase 2: interleaved GETs — GET /v1/jobs/{id} and GET /v1/me
    # -----------------------------------------------------------------------
    get_results = []

    def do_get_job(jid):
        r = client.get(f"/v1/jobs/{jid}", headers=headers)
        return r.status_code, r.text

    def do_get_me():
        r = client.get("/v1/me", headers=headers)
        return r.status_code, r.text

    # Mix job-gets and me-gets concurrently
    with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
        futures = []
        for jid in created_job_ids:
            futures.append(ex.submit(do_get_job, jid))
        # Add N_WORKERS/2 /v1/me calls interleaved
        for _ in range(N_WORKERS // 2):
            futures.append(ex.submit(do_get_me))

        for fut in as_completed(futures):
            status, body = fut.result()
            get_results.append((status, body))

    get_statuses = [s for s, _ in get_results]
    locked_get_bodies = [b for s, b in get_results if "database is locked" in b.lower()]

    assert not locked_get_bodies, (
        f"'database is locked' in {len(locked_get_bodies)} GET response(s): "
        f"{locked_get_bodies[:3]}"
    )
    assert all(s == 200 for s in get_statuses), (
        f"Expected all 200, got distribution: {sorted(set(get_statuses))}"
    )

    # -----------------------------------------------------------------------
    # Phase 3: mixed concurrent reads + writes together
    # -----------------------------------------------------------------------
    mixed_results = []

    def do_post_w(i):
        r = client.post(
            "/v1/jobs",
            json={"prompt": f"mixed writer {i}"},
            headers=headers,
        )
        return "post", r.status_code, r.text

    def do_get_w(jid):
        r = client.get(f"/v1/jobs/{jid}", headers=headers)
        return "get", r.status_code, r.text

    with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
        futures = []
        # 10 writers + 10 readers submitted simultaneously
        for i in range(10):
            futures.append(ex.submit(do_post_w, i + 100))
        for jid in created_job_ids[:10]:
            futures.append(ex.submit(do_get_w, jid))

        for fut in as_completed(futures):
            kind, status, body = fut.result()
            mixed_results.append((kind, status, body))

    locked_mixed = [b for _, _, b in mixed_results if "database is locked" in b.lower()]
    assert not locked_mixed, (
        f"'database is locked' in mixed phase: {locked_mixed[:3]}"
    )

    post_mixed = [(s, b) for k, s, b in mixed_results if k == "post"]
    get_mixed = [(s, b) for k, s, b in mixed_results if k == "get"]

    assert all(s == 201 for s, _ in post_mixed), (
        f"Mixed-phase POST failures: {[s for s,_ in post_mixed if s != 201]}"
    )
    assert all(s == 200 for s, _ in get_mixed), (
        f"Mixed-phase GET failures: {[s for s,_ in get_mixed if s != 200]}"
    )

    # -----------------------------------------------------------------------
    # Summary assertion: ZERO 500s across ALL phases
    # -----------------------------------------------------------------------
    all_statuses = post_statuses + get_statuses + [s for _, s, _ in mixed_results]
    five_hundreds = [s for s in all_statuses if s == 500]
    assert not five_hundreds, (
        f"{len(five_hundreds)} 500 responses across all phases"
    )
