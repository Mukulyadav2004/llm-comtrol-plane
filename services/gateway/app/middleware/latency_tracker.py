"""Rolling latency tracker — stores per-route request durations in Redis
and computes P50/P95/P99 over a sliding 5-minute window.

Used by the hedged request engine to decide when to fire a secondary request,
and by the /v1/usage endpoint to surface performance stats.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

import redis as redis_lib

from app.config import settings

_redis = redis_lib.from_url(settings.redis_url, decode_responses=True)

_WINDOW_SECONDS = 300  # 5 minutes
_MAX_SAMPLES = 500     # cap per route to bound memory


def record_latency(route_name: str, duration_ms: float) -> None:
    key = f"latency:{route_name}"
    now = time.time()
    # score = timestamp, value = "<timestamp>:<duration_ms>" for uniqueness
    member = f"{now:.6f}:{duration_ms:.2f}"
    pipe = _redis.pipeline()
    pipe.zadd(key, {member: now})
    pipe.zremrangebyscore(key, 0, now - _WINDOW_SECONDS)
    # Trim to max samples to prevent unbounded growth
    pipe.zremrangebyrank(key, 0, -((_MAX_SAMPLES + 1)))
    pipe.execute()


def get_percentile(route_name: str, percentile: float = 0.95) -> Optional[float]:
    """Return P{percentile*100} latency in ms over the last 5 minutes, or None if no data."""
    key = f"latency:{route_name}"
    now = time.time()
    members = _redis.zrangebyscore(key, now - _WINDOW_SECONDS, now)
    if not members:
        return None
    durations = sorted(float(m.split(":")[1]) for m in members)
    idx = int(len(durations) * percentile)
    return durations[min(idx, len(durations) - 1)]


def get_stats(route_name: str) -> Dict[str, Optional[float]]:
    key = f"latency:{route_name}"
    now = time.time()
    members = _redis.zrangebyscore(key, now - _WINDOW_SECONDS, now)
    if not members:
        return {"p50_ms": None, "p95_ms": None, "p99_ms": None, "sample_count": 0}
    durations = sorted(float(m.split(":")[1]) for m in members)
    n = len(durations)
    return {
        "p50_ms": durations[int(n * 0.50)],
        "p95_ms": durations[int(n * 0.95)],
        "p99_ms": durations[int(n * 0.99)],
        "sample_count": n,
    }


def get_fastest_route(route_names: List[str]) -> str:
    """Given a list of equivalent routes, return the one with the lowest P95.

    Falls back to the first route if no latency data exists for any.
    """
    best_route = route_names[0]
    best_p95 = float("inf")

    for name in route_names:
        p95 = get_percentile(name, 0.95)
        if p95 is not None and p95 < best_p95:
            best_p95 = p95
            best_route = name

    return best_route
