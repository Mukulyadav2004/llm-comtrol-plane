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

import logging
import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Deque, Optional

import redis as redis_lib

from app.config import settings
from app.middleware.redis_safety import REDIS_ERRORS, REDIS_EXCEPTIONS

log = logging.getLogger(__name__)

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
    #: True when Redis was unreachable and this verdict came from the configured
    #: degraded mode rather than the shared window.
    degraded: bool = False

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.current)

    @property
    def retry_after_seconds(self) -> int:
        # Round up — advertising 0 seconds on a denied request is useless.
        return max(1, -(-self.retry_after_ms // 1000))


class _LocalWindow:
    """Per-process sliding window used when Redis is unreachable.

    Not a substitute for the shared limiter: N replicas will together admit up
    to N x the configured rate. But "roughly N times the limit" is a very
    different outage from "unlimited", which is what a plain fail-open does to
    an endpoint that spends money on every call.

    Bounded on purpose. Under an attack the key space is attacker-controlled, so
    a plain dict here would turn a Redis outage into a memory exhaustion bug.
    """

    def __init__(self, max_keys: int = 10_000):
        self._windows: "OrderedDict[str, Deque[float]]" = OrderedDict()
        self._max_keys = max_keys
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: float = 60.0) -> bool:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            hits = self._windows.get(key)
            if hits is None:
                hits = deque()
                self._windows[key] = hits
            self._windows.move_to_end(key)

            while hits and hits[0] <= cutoff:
                hits.popleft()

            if len(hits) >= limit:
                return False

            hits.append(now)

            while len(self._windows) > self._max_keys:
                self._windows.popitem(last=False)
            return True

    def clear(self) -> None:
        with self._lock:
            self._windows.clear()


_local_window = _LocalWindow()


def _degraded_decision(key: str, rpm_limit: int, exc: Exception) -> RateLimitDecision:
    """Apply the configured policy when the shared limiter is unavailable.

    There is no free choice here, only a choice of which failure to prefer:

      local  (default) each replica enforces the limit on its own. Accuracy
             degrades to roughly N x limit across N replicas; availability and
             cost control both survive.
      open   admit everything. The gateway stays up and spend becomes unbounded
             for the duration of the outage.
      closed reject everything with a 503. Spend is capped and the gateway is
             down, which turns a Redis blip into a total outage.
    """
    mode = settings.rate_limit_degraded_mode
    REDIS_ERRORS.labels(operation="rate_limit").inc()
    log.error("rate_limit.backend_unavailable mode=%s error=%s", mode, exc)

    if mode == "closed":
        allowed = False
    elif mode == "open":
        allowed = True
    else:
        allowed = _local_window.check(key, rpm_limit)

    return RateLimitDecision(
        allowed=allowed,
        current=0 if allowed else rpm_limit,
        limit=rpm_limit,
        retry_after_ms=1_000,
        degraded=True,
    )


def check_rate_limit_detailed(
    route_name: str,
    rpm_limit: int,
    client_id: str = "global",
) -> RateLimitDecision:
    """Atomically test-and-consume one slot in the caller's 60s window.

    Redis is a hard dependency of *this* check by design — a limiter that shares
    no state is not a limiter. What it must not be is a hard dependency of the
    request: when the backend is unreachable the configured degraded mode
    decides, and the caller can tell the difference from `decision.degraded`.
    """
    key = f"rl:{route_name}:{client_id}"
    window_ms = 60_000
    now_ms = int(time.time() * 1000)
    member = f"{now_ms}:{uuid.uuid4().hex}"

    try:
        allowed, count, retry_after = _get_script()(
            keys=[key], args=[now_ms, window_ms, rpm_limit, member]
        )
    except REDIS_EXCEPTIONS as exc:
        return _degraded_decision(key, rpm_limit, exc)

    return RateLimitDecision(
        allowed=bool(int(allowed)),
        current=int(count),
        limit=rpm_limit,
        retry_after_ms=int(retry_after),
    )


def check_rate_limit(route_name: str, rpm_limit: int, client_id: str = "global") -> bool:
    """Returns True if the request is allowed, False if rate-limited."""
    return check_rate_limit_detailed(route_name, rpm_limit, client_id).allowed
