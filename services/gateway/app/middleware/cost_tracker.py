"""Track cumulative token cost per route in Redis.

Note the known limitation this does *not* fix: these are counters, not a spend
log. There is no per-request row, so cost cannot be attributed to a key, user or
team, cannot be queried historically, and does not survive a Redis flush. Moving
this to an append-only Postgres table is the next step, not this one.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import redis as redis_lib

from app.config import settings
from app.middleware.redis_safety import redis_safe

_redis = redis_lib.from_url(settings.redis_url, decode_responses=True)

_SCAN_BATCH = 500


@redis_safe(operation="record_usage")
def record_usage(
    route_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_per_1k: float,
) -> None:
    total_tokens = prompt_tokens + completion_tokens
    cost = (total_tokens / 1000) * cost_per_1k

    pipe = _redis.pipeline()
    pipe.incrbyfloat(f"cost:{route_name}:total_usd", cost)
    pipe.incrby(f"cost:{route_name}:total_tokens", total_tokens)
    pipe.incrby(f"cost:{route_name}:prompt_tokens", prompt_tokens)
    pipe.incrby(f"cost:{route_name}:completion_tokens", completion_tokens)
    pipe.incrby(f"cost:{route_name}:requests", 1)
    pipe.execute()


@redis_safe(lambda: [None] * 5, operation="get_route_stats")
def _read_counters(route_name: str) -> List[Optional[str]]:
    pipe = _redis.pipeline()
    pipe.get(f"cost:{route_name}:total_usd")
    pipe.get(f"cost:{route_name}:total_tokens")
    pipe.get(f"cost:{route_name}:prompt_tokens")
    pipe.get(f"cost:{route_name}:completion_tokens")
    pipe.get(f"cost:{route_name}:requests")
    return pipe.execute()


def get_route_stats(route_name: str) -> Dict[str, float]:
    """Counters for one route. Reports zeroes rather than raising when Redis is
    unreachable — a dashboard with a gap in it beats a dashboard that 500s."""
    usd, total, prompt, completion, requests = _read_counters(route_name)

    return {
        "route": route_name,
        "total_usd": float(usd or 0),
        "total_tokens": int(total or 0),
        "prompt_tokens": int(prompt or 0),
        "completion_tokens": int(completion or 0),
        "total_requests": int(requests or 0),
    }


@redis_safe(list, operation="list_tracked_routes")
def list_tracked_routes() -> List[str]:
    """Route names with recorded usage.

    Uses SCAN, not KEYS. KEYS walks the entire keyspace in a single blocking
    call — and the dashboard's Usage tab polls this on a timer, so on a shared
    Redis it stalls every other client on every refresh.
    """
    names = set()
    for key in _redis.scan_iter(match="cost:*:requests", count=_SCAN_BATCH):
        parts = key.split(":")
        if len(parts) >= 3:
            names.add(":".join(parts[1:-1]))
    return sorted(names)


def get_all_stats() -> List[Dict[str, float]]:
    return [get_route_stats(name) for name in list_tracked_routes()]
