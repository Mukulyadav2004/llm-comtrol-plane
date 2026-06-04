"""Per-tool sliding-window rate limiter backed by Redis."""
import time
import redis as redis_lib
from app.config import settings

_redis = redis_lib.from_url(settings.redis_url, decode_responses=True)

_DEFAULT_RPM = 120


def check_tool_rate_limit(tool_name: str, client_id: str, rpm: int = _DEFAULT_RPM) -> bool:
    key = f"mcp_rl:{tool_name}:{client_id}"
    now = int(time.time())
    window_start = now - 60

    pipe = _redis.pipeline()
    pipe.zremrangebyscore(key, 0, window_start)
    pipe.zadd(key, {f"{now}:{time.monotonic_ns()}": now})
    pipe.zcard(key)
    pipe.expire(key, 70)
    results = pipe.execute()
    return results[2] <= rpm
