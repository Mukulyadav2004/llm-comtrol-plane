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


@pytest.fixture
def mock_http():
    """Point a provider's HTTP client at an httpx.MockTransport handler.

    Adapters build their client through `BaseProvider._client`, so swapping that
    one method lets a test assert on the exact bytes an adapter puts on the wire
    and feed it exact bytes back — no server, no global httpx patching.
    """
    import httpx

    def _install(provider, handler):
        seen = []

        def _recording(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        def _factory(req):
            return httpx.AsyncClient(
                transport=httpx.MockTransport(_recording), timeout=req.timeout
            )

        provider._client = _factory
        return seen

    return _install


@pytest.fixture
def provider_request():
    """A ProviderRequest with sane defaults; override any field by keyword."""
    from app.providers.base import ProviderRequest

    def _make(**overrides):
        base = {
            "model": "test-model",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 128,
            "temperature": 0.5,
        }
        base.update(overrides)
        return ProviderRequest(**base)

    return _make
