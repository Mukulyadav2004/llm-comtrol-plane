"""Redis is telemetry, not the request path.

Regression tests for a bug hit in production: a Redis blip during
`record_latency` was raised inside the router's try block, reported as a
provider error, and — a ConnectionError not being a non-retryable
ProviderError — fell through to the fallback route. A response that had already
succeeded and been paid for was discarded, and a second upstream call was billed
to replace it.
"""
import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from app import main as app_main
from app.middleware import cost_tracker, latency_tracker, rate_limit
from app.providers.base import BaseProvider
from app.router import llm_router


class _DeadRedis:
    """Every operation fails the way an unreachable Redis fails."""

    def __getattr__(self, name):
        def _boom(*args, **kwargs):
            raise RedisConnectionError("Error 111 connecting to redis:6379. Connection refused.")
        return _boom


@pytest.fixture
def dead_redis(monkeypatch):
    """Take Redis away from every module that reads or writes it."""
    monkeypatch.setattr(latency_tracker, "_redis", _DeadRedis())
    monkeypatch.setattr(cost_tracker, "_redis", _DeadRedis())

    def _dead_script():
        def _call(*args, **kwargs):
            raise RedisConnectionError("Connection refused")
        return _call

    monkeypatch.setattr(rate_limit, "_get_script", _dead_script)
    rate_limit._local_window.clear()
    yield
    rate_limit._local_window.clear()


# ── telemetry degrades quietly ────────────────────────────────────────────────

def test_recording_latency_does_not_raise(dead_redis):
    latency_tracker.record_latency("r", 12.5)


def test_recording_usage_does_not_raise(dead_redis):
    cost_tracker.record_usage("r", 10, 5, 0.002)


def test_reading_a_percentile_returns_no_data(dead_redis):
    assert latency_tracker.get_percentile("r", 0.95) is None


def test_reading_latency_stats_returns_the_empty_shape(dead_redis):
    stats = latency_tracker.get_stats("r")
    assert stats == {"p50_ms": None, "p95_ms": None, "p99_ms": None, "sample_count": 0}


def test_reading_cost_stats_still_names_the_route(dead_redis):
    """The fallback has to answer for the route that was asked about, not a
    blank one, or the dashboard renders rows with no name."""
    stats = cost_tracker.get_route_stats("my-route")
    assert stats["route"] == "my-route"
    assert stats["total_usd"] == 0.0
    assert stats["total_requests"] == 0


def test_listing_tracked_routes_returns_empty(dead_redis):
    assert cost_tracker.list_tracked_routes() == []


def test_a_programming_error_is_still_raised(monkeypatch):
    """The exception list is narrow on purpose. Swallowing bare Exception here
    would hide the TypeErrors that mean this code is wrong."""
    class _Wrong:
        def __getattr__(self, name):
            def _boom(*a, **k):
                raise TypeError("record_latency called with the wrong shape")
            return _boom

    monkeypatch.setattr(latency_tracker, "_redis", _Wrong())
    with pytest.raises(TypeError):
        latency_tracker.record_latency("r", 1.0)


# ── the live bug ──────────────────────────────────────────────────────────────

@pytest.fixture
def counting_provider(monkeypatch):
    """Count upstream calls so a spurious fallback is visible."""
    calls = []

    class _Stub(BaseProvider):
        name = "ollama"

        async def chat_completion(self, req):
            calls.append(req.model)
            return {
                "id": "x", "object": "chat.completion", "model": req.model,
                "choices": [{"index": 0,
                             "message": {"role": "assistant", "content": "the answer"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }

        async def stream_chat_completion(self, req):
            yield {}

    monkeypatch.setattr(llm_router, "get_provider", lambda name: _Stub())
    monkeypatch.setattr(llm_router, "get_all_guardrails_for_route", lambda r: [])
    monkeypatch.setattr(llm_router, "get_observability_configs", lambda: [])
    return calls


@pytest.mark.asyncio
async def test_a_redis_blip_does_not_discard_a_successful_response(
    dead_redis, counting_provider, monkeypatch, route
):
    table = {"solo": route("solo")}
    monkeypatch.setattr(llm_router, "get_route", lambda n: table.get(n))

    result = await llm_router.route_request(
        "solo", [{"role": "user", "content": "hi"}])

    assert result["choices"][0]["message"]["content"] == "the answer"
    assert result["usage"]["total_tokens"] == 15


@pytest.mark.asyncio
async def test_a_redis_blip_does_not_bill_a_second_upstream_call(
    dead_redis, counting_provider, monkeypatch, route
):
    """The expensive half of the bug: the discarded response fell through to the
    hedge route, so the outage doubled the bill as well as losing the answer."""
    table = {"primary": route("primary", fallback="backup"), "backup": route("backup")}
    monkeypatch.setattr(llm_router, "get_route", lambda n: table.get(n))

    result = await llm_router.route_request(
        "primary", [{"role": "user", "content": "hi"}])

    assert result["choices"][0]["message"]["content"] == "the answer"
    assert len(counting_provider) == 1, (
        f"expected one upstream call, got {len(counting_provider)}"
    )


@pytest.mark.asyncio
async def test_the_hedge_threshold_survives_a_dead_redis(
    dead_redis, counting_provider, monkeypatch, route
):
    """_compute_hedge_threshold reads a percentile from Redis before the try
    block, so a blip there failed the request before any provider was called."""
    table = {"primary": route("primary", fallback="backup"), "backup": route("backup")}
    monkeypatch.setattr(llm_router, "get_route", lambda n: table.get(n))

    result = await llm_router.route_request(
        "primary", [{"role": "user", "content": "hi"}])
    assert result["choices"][0]["message"]["content"] == "the answer"


# ── rate limiter degraded modes ───────────────────────────────────────────────

def test_local_mode_still_enforces_the_limit_per_replica(dead_redis, monkeypatch):
    """Not exact across replicas, but 'roughly N x limit' is a very different
    outage from 'unlimited' on an endpoint that spends money per call."""
    monkeypatch.setattr(rate_limit.settings, "rate_limit_degraded_mode", "local")
    allowed = [rate_limit.check_rate_limit("r", 3, "c") for _ in range(6)]
    assert allowed == [True, True, True, False, False, False]


def test_local_mode_isolates_clients(dead_redis, monkeypatch):
    monkeypatch.setattr(rate_limit.settings, "rate_limit_degraded_mode", "local")
    for _ in range(3):
        rate_limit.check_rate_limit("r", 3, "noisy")
    assert rate_limit.check_rate_limit("r", 3, "noisy") is False
    assert rate_limit.check_rate_limit("r", 3, "quiet") is True


def test_open_mode_admits_everything(dead_redis, monkeypatch):
    monkeypatch.setattr(rate_limit.settings, "rate_limit_degraded_mode", "open")
    assert all(rate_limit.check_rate_limit("r", 1, "c") for _ in range(10))


def test_closed_mode_rejects_everything(dead_redis, monkeypatch):
    monkeypatch.setattr(rate_limit.settings, "rate_limit_degraded_mode", "closed")
    assert rate_limit.check_rate_limit("r", 100, "c") is False


def test_a_degraded_verdict_is_marked_as_such(dead_redis, monkeypatch):
    monkeypatch.setattr(rate_limit.settings, "rate_limit_degraded_mode", "open")
    assert rate_limit.check_rate_limit_detailed("r", 10, "c").degraded is True


def test_a_normal_verdict_is_not_marked_degraded():
    assert rate_limit.check_rate_limit_detailed("r", 10, "c").degraded is False


def test_the_local_window_is_bounded(dead_redis, monkeypatch):
    """Under attack the key space is attacker-controlled, so an unbounded dict
    would turn a Redis outage into memory exhaustion."""
    monkeypatch.setattr(rate_limit.settings, "rate_limit_degraded_mode", "local")
    window = rate_limit._LocalWindow(max_keys=50)
    for i in range(500):
        window.check(f"key-{i}", 10)
    assert len(window._windows) <= 50


# ── what the caller is told ───────────────────────────────────────────────────

@pytest.fixture
def client():
    return TestClient(app_main.app)


@pytest.fixture
def one_route(monkeypatch, route):
    r = route("solo")
    monkeypatch.setattr(llm_router, "get_route", lambda n: r if n == "solo" else None)
    monkeypatch.setattr(llm_router, "get_all_guardrails_for_route", lambda r_: [])
    monkeypatch.setattr(llm_router, "get_observability_configs", lambda: [])
    return r


def test_a_dead_limiter_in_closed_mode_is_503_not_429(
    client, dead_redis, one_route, counting_provider, monkeypatch
):
    """429 would tell the caller they exceeded a quota they did not exceed, and
    send them to back off rather than to page whoever owns Redis."""
    monkeypatch.setattr(rate_limit.settings, "rate_limit_degraded_mode", "closed")
    resp = client.post("/v1/chat/completions", json={
        "model": "solo", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 503
    assert resp.headers.get("Retry-After") == "5"
    assert "unavailable" in resp.json()["detail"].lower()


def test_a_dead_redis_still_serves_the_request_end_to_end(
    client, dead_redis, one_route, counting_provider, monkeypatch
):
    monkeypatch.setattr(rate_limit.settings, "rate_limit_degraded_mode", "local")
    resp = client.post("/v1/chat/completions", json={
        "model": "solo", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "the answer"
