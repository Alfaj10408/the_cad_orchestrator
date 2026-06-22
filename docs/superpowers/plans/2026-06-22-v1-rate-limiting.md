# Rate Limiting (P2) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Per-API-key token-bucket rate limiting at the `/v1` surface via one ASGI middleware, with `X-RateLimit-*` headers on success and `Retry-After` + locked 429 body on denial.

**Architecture:** New pure module `backend/app/v1/ratelimit.py` (config-driven token buckets, `check()`, classifier, `reset()`); a `BaseHTTPMiddleware` registered in `main.py` **before** CORS so CORS stays outermost. `routes.py` untouched. In-memory single-instance, Redis-swappable behind `check()`.

**Tech Stack:** Python 3.11 (`/root/anaconda3/envs/cadskills/bin/python`), FastAPI/Starlette, pytest.

## Global Constraints
- Engine frozen: **never** modify `backend/app/services` or `backend/app/orchestrator` — STOP/BLOCKED if a task seems to need it. Guard `git diff v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator` must stay empty.
- No CAD/frontend/benchmark/schema changes. No `routes.py` change. No `db.*` change.
- Edits ONLY in `backend/app/v1/ratelimit.py` (new), `backend/app/main.py`, `backend/app/core/config.py`, `tests/test_v1_*`.
- Run from product root with cadskills python. Guard-check each commit.
- Locked 429 body shape: `{"detail": "...", "scope": "rate_limit", "retry_after": N}` + `Retry-After: N` header.
- Success responses on rate-limited categories carry `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`.
- Starlette ordering: `add_middleware` is reverse-registration (last added = outermost). To get CORS→RateLimit→routes, register the rate-limit middleware **before** the CORS `add_middleware` block.

---

## Task 1 — config knobs + `ratelimit.py` core (pure, unit-tested)

**Files:**
- Modify: `backend/app/core/config.py`
- Create: `backend/app/v1/ratelimit.py`
- Test: `tests/test_v1_ratelimit_unit.py`

**Interfaces — Produces:**
- `config.API_RATE_LIMIT_ENABLED: bool`, `API_RATE_SUBMIT_PER_MIN=10`, `API_RATE_READ_PER_MIN=120`, `API_RATE_SSE_PER_MIN=30`, `API_RATE_ADMIN_PER_MIN=60`, `API_RATE_MAX_BUCKETS=10000` (ints).
- `ratelimit.classify(method: str, path: str) -> str | None` → `"submit"|"read"|"sse"|"admin"|None` (None = not limited / exempt).
- `ratelimit.check(scope_id: str, category: str, now: float | None = None) -> Decision` where `Decision` is a dataclass `(allowed: bool, limit: int, remaining: int, reset: int, retry_after: int)`.
- `ratelimit.reset() -> None` (clears `_buckets`; for tests).

- [ ] **Step 1: Write the failing test**
```python
# tests/test_v1_ratelimit_unit.py
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
```

- [ ] **Step 2: Run, verify fail** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_ratelimit_unit.py -v`
Expected: FAIL (`No module named 'app.v1.ratelimit'` / missing config attrs).

- [ ] **Step 3: Implement config** — add to `backend/app/core/config.py` after the quota block (lines ~78-80):
```python
# --- /v1 rate limiting (P2) ---
API_RATE_LIMIT_ENABLED = _flag("API_RATE_LIMIT_ENABLED", "1")
API_RATE_SUBMIT_PER_MIN = int(os.environ.get("API_RATE_SUBMIT_PER_MIN", "10"))
API_RATE_READ_PER_MIN = int(os.environ.get("API_RATE_READ_PER_MIN", "120"))
API_RATE_SSE_PER_MIN = int(os.environ.get("API_RATE_SSE_PER_MIN", "30"))
API_RATE_ADMIN_PER_MIN = int(os.environ.get("API_RATE_ADMIN_PER_MIN", "60"))
API_RATE_MAX_BUCKETS = int(os.environ.get("API_RATE_MAX_BUCKETS", "10000"))
```
(`_flag` already exists in config.py — same helper used by `API_QUOTA_ENABLED`.)

- [ ] **Step 4: Implement `ratelimit.py`** — create `backend/app/v1/ratelimit.py`:
```python
"""In-process token-bucket rate limiting for the /v1 surface (P2).

Single-instance, no DB. Accessed only from the async middleware (one event
loop thread), so the read-modify-write on _buckets needs no lock. The check()
interface is Redis-swappable later.
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass

from app.core import config


@dataclass
class Decision:
    allowed: bool
    limit: int
    remaining: int
    reset: int          # seconds until bucket is full again
    retry_after: int    # seconds until >=1 token (only meaningful when denied)


# (tokens, last_refill_monotonic) per (scope_id, category)
_buckets: dict[tuple[str, str], list[float]] = {}

_RE_EVENTS = re.compile(r"^/v1/jobs/[^/]+/events/?$")
_RE_JOB_SUB = re.compile(r"^/v1/jobs/[^/]+(/.*)?$")   # any /v1/jobs/{id}... (read)


def _limit_for(category: str) -> int:
    return {
        "submit": config.API_RATE_SUBMIT_PER_MIN,
        "read": config.API_RATE_READ_PER_MIN,
        "sse": config.API_RATE_SSE_PER_MIN,
        "admin": config.API_RATE_ADMIN_PER_MIN,
    }[category]


def classify(method: str, path: str) -> str | None:
    """Map an HTTP method+path to a rate category, or None if not limited."""
    if path in ("/v1/healthz", "/v1/readyz"):
        return None
    if not path.startswith("/v1/"):
        return None
    if path.startswith("/v1/admin/"):
        return "admin"
    if _RE_EVENTS.match(path):
        return "sse"
    if path == "/v1/jobs" and method.upper() == "POST":
        return "submit"
    if path == "/v1/me":
        return "read"
    if _RE_JOB_SUB.match(path):     # /v1/jobs/{id}, /artifacts, /cancel, ...
        return "read"
    return None


def _sweep() -> None:
    """Drop fully-refilled (idle) buckets when the store grows too large."""
    if len(_buckets) <= config.API_RATE_MAX_BUCKETS:
        return
    for k in [k for k, v in _buckets.items()
              if v[0] >= _limit_for(k[1])]:
        _buckets.pop(k, None)


def check(scope_id: str, category: str, now: float | None = None) -> Decision:
    """Take one token from (scope_id, category). Lazy continuous refill."""
    cap = _limit_for(category)
    rate = cap / 60.0
    t = time.monotonic() if now is None else now
    key = (scope_id, category)
    bucket = _buckets.get(key)
    if bucket is None:
        _sweep()
        bucket = [float(cap), t]
        _buckets[key] = bucket
    tokens, last = bucket
    tokens = min(float(cap), tokens + (t - last) * rate)
    if tokens >= 1.0:
        tokens -= 1.0
        allowed = True
    else:
        allowed = False
    bucket[0], bucket[1] = tokens, t
    remaining = int(math.floor(tokens))
    deficit = cap - tokens
    reset = int(math.ceil(deficit / rate)) if deficit > 0 else 0
    retry_after = 0 if allowed else max(1, int(math.ceil((1.0 - tokens) / rate)))
    return Decision(allowed, cap, remaining, reset, retry_after)


def reset() -> None:
    _buckets.clear()
```

- [ ] **Step 5: Run, verify pass** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_ratelimit_unit.py -v` → all pass.

- [ ] **Step 6: Engine-freeze guard + commit**
```bash
cd /root/all_project_models/alfaj/text-to-cad-product
git diff --stat v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator   # must be empty
git add backend/app/core/config.py backend/app/v1/ratelimit.py tests/test_v1_ratelimit_unit.py
git commit -m "feat(v1): token-bucket rate limit core + config knobs (P2)"
```

**Success:** classify maps every category + exempt/None; bucket allows capacity then denies; refill/retry_after/reset/remaining math correct; scopes+categories independent; sweep evicts idle buckets; guard empty.

---

## Task 2 — middleware + `main.py` wiring (integration-tested)

**Files:**
- Modify: `backend/app/main.py`
- Create (in ratelimit.py): `RateLimitMiddleware` class (add to the file from Task 1)
- Test: `tests/test_v1_ratelimit_mw.py`
- Modify (regression safety): `tests/test_v1_cors.py`, `tests/test_v1_integration.py`

**Interfaces — Consumes:** `ratelimit.classify`, `ratelimit.check`, `ratelimit.Decision`, `config.API_RATE_LIMIT_ENABLED`; `auth.hash_key` (pure sha256, no DB). **Produces:** `ratelimit.RateLimitMiddleware(BaseHTTPMiddleware)`; `main.app` enforces limits when enabled.

- [ ] **Step 1: Write the failing test**
```python
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
```

- [ ] **Step 2: Run, verify fail** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_ratelimit_mw.py -v`
Expected: FAIL (`module 'app.v1.ratelimit' has no attribute 'RateLimitMiddleware'`).

- [ ] **Step 3: Implement the middleware** — append to `backend/app/v1/ratelimit.py`:
```python
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.v1 import auth as _auth


def _scope_id(request) -> str:
    raw = request.headers.get("authorization")
    token = _auth._bearer(raw)        # returns the bearer token or None
    if token:
        return "key:" + _auth.hash_key(token)
    client = request.client.host if request.client else "unknown"
    return "ip:" + client


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if not config.API_RATE_LIMIT_ENABLED:
            return await call_next(request)
        category = classify(request.method, request.url.path)
        if category is None:
            return await call_next(request)
        d = check(_scope_id(request), category)
        headers = {
            "X-RateLimit-Limit": str(d.limit),
            "X-RateLimit-Remaining": str(d.remaining),
            "X-RateLimit-Reset": str(d.reset),
        }
        if not d.allowed:
            headers["Retry-After"] = str(d.retry_after)
            return JSONResponse(
                status_code=429,
                content={
                    "detail": f"rate limit exceeded for {category}",
                    "scope": "rate_limit",
                    "retry_after": d.retry_after,
                },
                headers=headers,
            )
        response = await call_next(request)
        for k, v in headers.items():
            response.headers[k] = v
        return response
```
(`_auth._bearer` already exists in `auth.py` and parses `Bearer <token>` → token|None; `hash_key` is pure sha256(salt+token), no DB.)

- [ ] **Step 4: Run, verify pass** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_ratelimit_mw.py -v` → all pass.

- [ ] **Step 5: Wire into `main.py`** — register the rate-limit middleware **before** the CORS block so CORS stays outermost. Add the import with the other `app.v1` imports (line ~22):
```python
from app.v1 import db as v1db, routes as v1routes
from app.v1.ratelimit import RateLimitMiddleware
```
Then, immediately **before** the `if V1_CORS_ORIGINS:` block (line 45), insert:
```python
from app.core.config import API_RATE_LIMIT_ENABLED
if API_RATE_LIMIT_ENABLED:
    app.add_middleware(RateLimitMiddleware)
```
(Registering this BEFORE the CORS `add_middleware` makes CORS the last-added → outermost → CORS preflight handled first, rate-limit inner. Do not move the router includes.)

- [ ] **Step 6: Regression safety for `main.app` tests** — `test_v1_cors.py` and `test_v1_integration.py` start `main.app`. Disable rate limiting in their env so existing assertions don't trip. In each test's env/reload helper, set before importing/reloading `main`:
```python
os.environ["API_RATE_LIMIT_ENABLED"] = "0"
```
(`test_v1_cors.py` already sets `API_KEY_SALT` in `_reload()` — add this line beside it. `test_v1_integration.py` sets its env at module/fixture setup — add it there. Match each file's existing env style.)

- [ ] **Step 7: Run mw + main.app regressions** —
```bash
cd /root/all_project_models/alfaj/text-to-cad-product
/root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_ratelimit_mw.py tests/test_v1_cors.py tests/test_v1_integration.py -v
```
Expected: all pass.

- [ ] **Step 8: Engine-freeze guard + commit**
```bash
git diff --stat v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator   # must be empty
git add backend/app/v1/ratelimit.py backend/app/main.py tests/test_v1_ratelimit_mw.py tests/test_v1_cors.py tests/test_v1_integration.py
git commit -m "feat(v1): rate-limit middleware + main wiring (CORS stays outermost) (P2)"
```

**Success:** burst→429 with body+Retry-After+X-RateLimit-*; success carries the three headers; exempt paths bypass; distinct keys/IPs independent; disabled→passthrough; CORS+integration suites green; guard empty.

---

## Task 3 — verification

**Files:** test only.

- [ ] **Step 1: /v1 suite** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_*.py -q` → all pass.
- [ ] **Step 2: full suite** — `/root/anaconda3/envs/cadskills/bin/python -m pytest tests/ -q` → all pass.
- [ ] **Step 3: engine-freeze guard** — `git diff --stat v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator` → empty.
- [ ] **Step 4: commit** (only if test-only fixups were needed) `git commit -m "test(v1): rate limiting full verification"` (skip if nothing to commit).

**Success:** /v1 + full suites green; guard empty.

---

## Self-review
**Spec coverage:** token-bucket per (scope,category) ✓ (T1 check); category table incl. cancel→read, me→read, events→sse, admin/* ✓ (T1 classify); scope_id key-hash else IP ✓ (T2 _scope_id); healthz/readyz exempt ✓; middleware after CORS / before routers via reverse-registration ✓ (T2 step5); X-RateLimit-Limit/Remaining/Reset on success ✓; Retry-After + locked body on 429 ✓; config knobs incl. ENABLED + MAX_BUCKETS ✓; sweep/bounded memory ✓ (T1); disabled passthrough ✓; no routes.py/schema/engine/frontend/benchmark changes ✓; regression for main.app tests ✓ (T2 step6).
**Placeholder scan:** none — all code blocks concrete.
**Type consistency:** `Decision(allowed,limit,remaining,reset,retry_after)` used identically in T1 and T2; `classify(method,path)`/`check(scope_id,category,now=None)`/`reset()` signatures match across tasks; `_auth._bearer`/`hash_key` referenced as they exist in auth.py.
