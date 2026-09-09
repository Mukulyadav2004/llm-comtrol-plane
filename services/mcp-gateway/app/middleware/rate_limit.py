"""Per-tool sliding-window rate limiter backed by Redis.

Carries the same two fixes the LLM gateway's limiter got, because it had the
same two bugs:

  * check-then-insert inside one Lua script, so a *rejected* call no longer
    occupies a slot in its own window. The pipeline version did ZADD before
    ZCARD, which meant a caller over the limit kept the window saturated with
    denials and extended their own lockout past the configured RPM.
  * an explicit failure mode. Redis being unreachable used to raise into the
    request and 500 every tool call.

The logic is duplicated rather than imported: each service is its own Docker
build context, with no shared package to put it in. Extracting a common library
is the right fix and is not this change.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Deque

import redis as redis_lib
from redis.exceptions import RedisError

from app.config import settings

log = logging.getLogger(__name__)

_redis = redis_lib.from_url(settings.redis_url, decode_responses=True)

_DEFAULT_RPM = 120
_REDIS_EXCEPTIONS = (RedisError, OSError)

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
  return 1
end

redis.call('PEXPIRE', key, window + 10000)
return 0
"""

_script = None


def _get_script():
    global _script
    if _script is None:
        _script = _redis.register_script(_SLIDING_WINDOW_LUA)
    return _script


class _LocalWindow:
    """Per-process fallback used when Redis is unreachable. Bounded, because
    under abuse the key space is attacker-controlled."""

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


def resolve_rpm(tags: dict) -> int:
    """Per-tool RPM from its tags (set at registration / discovery), else default."""
    try:
        return int(tags.get("rate_limit_rpm", _DEFAULT_RPM))
    except (TypeError, ValueError):
        return _DEFAULT_RPM


def check_tool_rate_limit(tool_name: str, client_id: str, rpm: int = _DEFAULT_RPM) -> bool:
    """True if the call is allowed. Never raises because Redis is down."""
    key = f"mcp_rl:{tool_name}:{client_id}"
    window_ms = 60_000
    now_ms = int(time.time() * 1000)
    member = f"{now_ms}:{uuid.uuid4().hex}"

    try:
        return bool(int(_get_script()(keys=[key], args=[now_ms, window_ms, rpm, member])))
    except _REDIS_EXCEPTIONS as exc:
        mode = getattr(settings, "rate_limit_degraded_mode", "local")
        log.error("mcp_rate_limit.backend_unavailable mode=%s error=%s", mode, exc)
        if mode == "closed":
            return False
        if mode == "open":
            return True
        return _local_window.check(key, rpm)
