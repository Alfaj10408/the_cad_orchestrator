import sys
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import math
import pytest
from app.v1 import ratelimit as rl
from app.core import config as cfg


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(cfg, "API_RATE_SUBMIT_PER_MIN", 10)
    monkeypatch.setattr(cfg, "API_RATE_READ_PER_MIN", 120)
    monkeypatch.setattr(cfg, "API_RATE_SSE_PER_MIN", 30)
    monkeypatch.setattr(cfg, "API_RATE_ADMIN_PER_MIN", 60)
    monkeypatch.setattr(cfg, "API_RATE_MAX_BUCKETS", 10000)
    rl.reset()
    yield
    rl.reset()


def test_classify():
    assert rl.classify("POST", "/v1/jobs") == "submit"
    assert rl.classify("GET", "/v1/jobs/abc") == "read"
    assert rl.classify("GET", "/v1/jobs/abc/artifacts") == "read"
    assert rl.classify("GET", "/v1/jobs/abc/artifacts/out/x.step") == "read"
    assert rl.classify("POST", "/v1/jobs/abc/cancel") == "read"
    assert rl.classify("GET", "/v1/me") == "read"
    assert rl.classify("GET", "/v1/jobs/abc/events") == "sse"
    assert rl.classify("POST", "/v1/admin/keys") == "admin"
    assert rl.classify("POST", "/v1/admin/users/u/quota") == "admin"
    assert rl.classify("GET", "/v1/healthz") is None
    assert rl.classify("GET", "/v1/readyz") is None
    assert rl.classify("GET", "/api/projects") is None
    assert rl.classify("GET", "/") is None


def test_allows_up_to_capacity_then_denies():
    t = 1000.0
    allowed = [rl.check("key:a", "submit", now=t).allowed for _ in range(10)]
    assert all(allowed)
    d = rl.check("key:a", "submit", now=t)
    assert d.allowed is False
    assert d.limit == 10
    assert d.remaining == 0
    assert d.retry_after >= 1


def test_remaining_decrements():
    t = 5000.0
    d1 = rl.check("key:b", "submit", now=t)
    assert d1.allowed and d1.limit == 10 and d1.remaining == 9
    d2 = rl.check("key:b", "submit", now=t)
    assert d2.remaining == 8


def test_refill_over_time():
    t = 0.0
    for _ in range(10):
        rl.check("key:c", "submit", now=t)          # drain
    assert rl.check("key:c", "submit", now=t).allowed is False
    # submit refills 10/60 = 0.1667 tok/s; after 6s ~1 token back
    assert rl.check("key:c", "submit", now=t + 6.1).allowed is True


def test_retry_after_math_on_empty():
    t = 0.0
    for _ in range(10):
        rl.check("key:d", "submit", now=t)
    d = rl.check("key:d", "submit", now=t)
    # rate = 10/60; need 1 token => ceil(1/(10/60)) = ceil(6) = 6
    assert d.retry_after == 6


def test_reset_seconds_decreases_as_bucket_fills():
    t = 0.0
    for _ in range(10):
        rl.check("key:e", "submit", now=t)           # empty -> reset = full refill time
    d_empty = rl.check("key:e", "submit", now=t)      # denied, bucket ~empty
    # capacity 10 at 10/60 => full refill ~60s
    assert 55 <= d_empty.reset <= 60


def test_independent_scopes_and_categories():
    t = 100.0
    for _ in range(10):
        rl.check("key:f", "submit", now=t)
    assert rl.check("key:f", "submit", now=t).allowed is False
    assert rl.check("key:g", "submit", now=t).allowed is True      # other key
    assert rl.check("key:f", "read", now=t).allowed is True        # other category


def test_sweep_evicts_idle_buckets(monkeypatch):
    monkeypatch.setattr(cfg, "API_RATE_MAX_BUCKETS", 5)
    t = 0.0
    # create 5 full (untouched-after-1) buckets, let them fully refill by using far-future now
    for i in range(5):
        rl.check(f"key:{i}", "read", now=t)
    # far future: all refilled to capacity (idle) -> next check triggers sweep
    rl.check("key:new", "read", now=t + 10_000)
    assert len(rl._buckets) <= 5
