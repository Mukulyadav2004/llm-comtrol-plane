"""Sliding-window rate limiter backed by Redis."""
import time
import uuid
from typing import Optional

import redis as redis_lib

from app.config import settings

_redis = redis_lib.from_url(settings.redis_url, decode_responses=True)


def check_rate_limit(route_name: str, rpm_limit: int, client_id: str = "global") -> bool:
    """Returns True if the request is allowed, False if rate-limited."""
    key = f"rl:{route_name}:{client_id}"
    window_ms = 60_000  # 1 minute
    now_ms = int(time.time() * 1000)
    window_start = now_ms - window_ms

    # Unique member per request — score is the timestamp (for window pruning),
    # member carries a random suffix so concurrent calls in the same ms don't collide.
    member = f"{now_ms}:{uuid.uuid4().hex}"

    pipe = _redis.pipeline()
    pipe.zremrangebyscore(key, 0, window_start)
    pipe.zadd(key, {member: now_ms})
    pipe.zcard(key)
    pipe.expire(key, 70)
    results = pipe.execute()

    current_count = results[2]
    return current_count <= rpm_limit
