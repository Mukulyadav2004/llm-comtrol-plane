"""Keep Redis failures out of the request's failure path.

Redis holds telemetry here: rolling latency percentiles and cost counters. None
of it is needed to answer a request, but all of it used to be able to fail one.
A blip during `record_latency` was raised inside the router's `try`, reported as
a provider error, and — because a ConnectionError is not a non-retryable
ProviderError — fell through to the fallback route and billed a second upstream
call for a response that had already succeeded and been paid for.

So: writing a metric may fail, and the request continues. Reading one may fail,
and the caller gets the "no data" answer it already knows how to handle.

The exception list is deliberately narrow. `RedisError` covers the client's own
failures (connection, timeout, bad response) and `OSError` covers socket-level
errors that escape it. Catching bare `Exception` here would swallow the
TypeErrors and KeyErrors that mean *this code* is wrong, which is exactly the
class of bug that should still be loud.
"""
from __future__ import annotations

import functools
import logging
from typing import Any, Callable, Optional, TypeVar

from prometheus_client import Counter
from redis.exceptions import RedisError

log = logging.getLogger(__name__)

T = TypeVar("T")

#: Incremented whenever a Redis operation was skipped because the backend was
#: unreachable. A non-zero rate means the numbers on the dashboard are missing
#: samples, and is the signal that Redis needs attention.
REDIS_ERRORS = Counter(
    "gateway_redis_errors_total",
    "Redis operations that failed and were degraded rather than raised",
    ["operation"],
)

REDIS_EXCEPTIONS = (RedisError, OSError)


def redis_safe(
    fallback: Any = None,
    *,
    operation: Optional[str] = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Degrade instead of raising when Redis is unreachable.

    `fallback` is returned on failure. Pass a zero-argument callable when the
    fallback is a mutable value, so callers never share one instance.
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        name = operation or fn.__name__

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except REDIS_EXCEPTIONS as exc:
                REDIS_ERRORS.labels(operation=name).inc()
                log.warning(
                    "redis.degraded operation=%s error=%s", name, exc, exc_info=False
                )
                return fallback() if callable(fallback) else fallback

        return wrapper

    return decorator
