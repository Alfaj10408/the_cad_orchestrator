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


def _sweep(now: float) -> None:
    """Drop fully-refilled (idle) buckets when the store grows too large."""
    if len(_buckets) < config.API_RATE_MAX_BUCKETS:
        return
    # Refill all buckets to check if they're at capacity
    for k, v in list(_buckets.items()):
        cat = k[1]
        cap = _limit_for(cat)
        rate = cap / 60.0
        tokens = v[0]
        last = v[1]
        tokens = min(float(cap), tokens + (now - last) * rate)
        if tokens >= float(cap):
            _buckets.pop(k, None)


def check(scope_id: str, category: str, now: float | None = None) -> Decision:
    """Take one token from (scope_id, category). Lazy continuous refill."""
    cap = _limit_for(category)
    rate = cap / 60.0
    t = time.monotonic() if now is None else now
    key = (scope_id, category)
    bucket = _buckets.get(key)
    if bucket is None:
        _sweep(t)
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
