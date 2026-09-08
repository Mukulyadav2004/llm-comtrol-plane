"""Test bootstrap.

`redis.from_url` is swapped for a single shared fakeredis instance *before* any
app module is imported, because the middleware modules build their client at
import time. Every test therefore talks to the same in-process Redis, flushed
between tests.
"""
import pathlib
import sys

import fakeredis
import pytest
import redis as redis_lib

GATEWAY_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))

_SHARED = fakeredis.FakeRedis(decode_responses=True)
redis_lib.from_url = lambda *args, **kwargs: _SHARED  # noqa: E731


@pytest.fixture(autouse=True)
def _flush_redis():
    _SHARED.flushall()
    yield
    _SHARED.flushall()


@pytest.fixture
def redis_client():
    return _SHARED


@pytest.fixture
def route():
    """A minimal enabled route, as the control plane would render it."""
    def _make(name="test-route", fallback=None, **policy):
        base_policy = {
            "rate_limit_rpm": 60,
            "max_tokens": 256,
            "temperature": 0.7,
            "cost_per_1k_tokens": 0.002,
        }
        base_policy.update(policy)
        return {
            "name": name,
            "provider": "ollama",
            "model": "llama3.2:1b",
            "base_url": "http://ollama:11434",
            "enabled": True,
            "fallback": fallback,
            "guardrail_ids": [],
            "policy": base_policy,
        }
    return _make
