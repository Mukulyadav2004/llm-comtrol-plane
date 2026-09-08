"""Sliding-window rate limiter backed by Redis.

The check and the insert happen inside a single Lua script so they are atomic
across gateway replicas. This matters for correctness in two ways:

  1. Two replicas cannot both observe "count < limit" and both admit a request.
  2. A *rejected* request is never written into the window.

(2) was a real bug in the naive pipeline version: it did ZADD then ZCARD, so
every denied request still occupied a slot. Once a caller crossed the limit,
their own retries kept the window saturated and the lockout stretched far past
the configured RPM. Denied traffic must not extend its own penalty.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Optional

import redis as redis_lib

from app.config import settings

_redis = redis_lib.from_url(settings.redis_url, decode_responses=True)

# KEYS[1] = window key
# ARGV[1] = now_ms, ARGV[2] = window_ms, ARGV[3] = limit, ARGV[4] = member
# Returns {allowed, current_count, retry_after_ms}
_SLIDING_WINDOW_LUA = """
local key    = KEYS[1]
local now    = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit  = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)

if count < limit then
  redis.call('ZADD', key, now, member)
  redis.call('PEXPIRE', key, window + 10000)
  return {1, count + 1, 0}
end

-- Denied. Do NOT add the member: a rejected request must not consume a slot.
redis.call('PEXPIRE', key, window + 10000)
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local retry_after = window
if oldest[2] then
  retry_after = math.max(0, (tonumber(oldest[2]) + window) - now)
end
return {0, count, retry_after}
"""

_script = None


def _get_script():
    global _script
    if _script is None:
        _script = _redis.register_script(_SLIDING_WINDOW_LUA)
    return _script


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    current: int
    limit: int
    retry_after_ms: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.current)

    @property
    def retry_after_seconds(self) -> int:
        # Round up — advertising 0 seconds on a denied request is useless.
        return max(1, -(-self.retry_after_ms // 1000))


def check_rate_limit_detailed(
    route_name: str,
    rpm_limit: int,
    client_id: str = "global",
) -> RateLimitDecision:
    """Atomically test-and-consume one slot in the caller's 60s window."""
    key = f"rl:{route_name}:{client_id}"
    window_ms = 60_000
    now_ms = int(time.time() * 1000)
    member = f"{now_ms}:{uuid.uuid4().hex}"

    allowed, count, retry_after = _get_script()(
        keys=[key], args=[now_ms, window_ms, rpm_limit, member]
    )
    return RateLimitDecision(
        allowed=bool(int(allowed)),
        current=int(count),
        limit=rpm_limit,
        retry_after_ms=int(retry_after),
    )


def check_rate_limit(route_name: str, rpm_limit: int, client_id: str = "global") -> bool:
    """Returns True if the request is allowed, False if rate-limited."""
    return check_rate_limit_detailed(route_name, rpm_limit, client_id).allowed
