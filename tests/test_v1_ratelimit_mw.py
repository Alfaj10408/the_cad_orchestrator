# tests/test_v1_ratelimit_mw.py
import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from app.v1 import ratelimit as rl
from app.core import config as cfg


def _app(monkeypatch, enabled=True):
    monkeypatch.setattr(cfg, "API_RATE_LIMIT_ENABLED", enabled)
    monkeypatch.setattr(cfg, "API_RATE_SUBMIT_PER_MIN", 3)
    monkeypatch.setattr(cfg, "API_RATE_READ_PER_MIN", 120)
    monkeypatch.setattr(cfg, "API_RATE_SSE_PER_MIN", 30)
    monkeypatch.setattr(cfg, "API_RATE_ADMIN_PER_MIN", 60)
    monkeypatch.setattr(cfg, "API_RATE_MAX_BUCKETS", 10000)
    rl.reset()
    app = FastAPI()
    app.add_middleware(rl.RateLimitMiddleware)
    # minimal /v1 endpoints mirroring real paths
    @app.post("/v1/jobs")
    def _submit():
        return {"ok": True}
    @app.get("/v1/me")
    def _me():
        return {"ok": True}
    @app.get("/v1/healthz")
    def _hz():
        return {"ok": True}
    return TestClient(app)


def test_submit_burst_then_429(monkeypatch):
    tc = _app(monkeypatch)
    h = {"Authorization": "Bearer abc"}
    for _ in range(3):
        r = tc.post("/v1/jobs", headers=h)
        assert r.status_code == 200
        assert r.headers["X-RateLimit-Limit"] == "3"
        assert "X-RateLimit-Remaining" in r.headers
        assert "X-RateLimit-Reset" in r.headers
    r = tc.post("/v1/jobs", headers=h)
    assert r.status_code == 429
    body = r.json()
    assert body["scope"] == "rate_limit"
    assert body["retry_after"] >= 1
    assert "detail" in body
    assert r.headers["Retry-After"] == str(body["retry_after"])
    assert "X-RateLimit-Limit" in r.headers


def test_read_higher_ceiling(monkeypatch):
    tc = _app(monkeypatch)
    h = {"Authorization": "Bearer abc"}
    # 10 reads well under 120 -> all 200
    for _ in range(10):
        assert tc.get("/v1/me", headers=h).status_code == 200


def test_healthz_exempt_no_headers(monkeypatch):
    tc = _app(monkeypatch)
    for _ in range(50):
        r = tc.get("/v1/healthz")
        assert r.status_code == 200
    assert "X-RateLimit-Limit" not in r.headers


def test_distinct_keys_independent(monkeypatch):
    tc = _app(monkeypatch)
    for _ in range(3):
        tc.post("/v1/jobs", headers={"Authorization": "Bearer k1"})
    assert tc.post("/v1/jobs", headers={"Authorization": "Bearer k1"}).status_code == 429
    assert tc.post("/v1/jobs", headers={"Authorization": "Bearer k2"}).status_code == 200


def test_unauthed_bucketed_by_ip(monkeypatch):
    tc = _app(monkeypatch)
    # no Authorization header -> IP scope; same client -> shares a bucket
    for _ in range(3):
        assert tc.post("/v1/jobs").status_code == 200
    assert tc.post("/v1/jobs").status_code == 429


def test_disabled_passthrough_no_headers(monkeypatch):
    tc = _app(monkeypatch, enabled=False)
    h = {"Authorization": "Bearer abc"}
    for _ in range(10):
        r = tc.post("/v1/jobs", headers=h)
        assert r.status_code == 200
    assert "X-RateLimit-Limit" not in r.headers
