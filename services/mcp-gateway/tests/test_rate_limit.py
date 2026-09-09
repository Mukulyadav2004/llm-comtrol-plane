"""Per-tool rate limiting, including what happens when Redis is not there."""
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.middleware import rate_limit
from app.middleware.rate_limit import check_tool_rate_limit, resolve_rpm


@pytest.fixture
def dead_redis(monkeypatch):
    def _dead():
        def _call(*args, **kwargs):
            raise RedisConnectionError("Connection refused")
        return _call
    monkeypatch.setattr(rate_limit, "_get_script", _dead)
    rate_limit._local_window.clear()
    yield
    rate_limit._local_window.clear()


def test_calls_under_the_limit_are_allowed():
    assert all(check_tool_rate_limit("search", "c", 5) for _ in range(5))


def test_the_limit_is_enforced():
    for _ in range(3):
        check_tool_rate_limit("search", "c", 3)
    assert check_tool_rate_limit("search", "c", 3) is False


def test_rejected_calls_do_not_consume_window_slots():
    """Same bug the LLM gateway's limiter had: ZADD before ZCARD meant denials
    kept the window full and stretched the lockout past the configured RPM."""
    for _ in range(2):
        check_tool_rate_limit("search", "spammer", 2)
    for _ in range(20):
        assert check_tool_rate_limit("search", "spammer", 2) is False
    # Two real calls, twenty denials, and the window still holds only two.
    assert rate_limit._redis.zcard("mcp_rl:search:spammer") == 2


def test_tools_and_clients_are_isolated():
    for _ in range(2):
        check_tool_rate_limit("search", "a", 2)
    assert check_tool_rate_limit("search", "a", 2) is False
    assert check_tool_rate_limit("search", "b", 2) is True
    assert check_tool_rate_limit("other", "a", 2) is True


def test_rpm_comes_from_tool_tags():
    assert resolve_rpm({"rate_limit_rpm": 7}) == 7
    assert resolve_rpm({"rate_limit_rpm": "nonsense"}) == 120
    assert resolve_rpm({}) == 120


def test_a_dead_redis_does_not_raise(dead_redis):
    """It used to 500 every tool call."""
    check_tool_rate_limit("search", "c", 5)


def test_a_dead_redis_still_limits_locally(dead_redis, monkeypatch):
    monkeypatch.setattr(rate_limit.settings, "rate_limit_degraded_mode", "local")
    assert [check_tool_rate_limit("search", "c", 2) for _ in range(4)] == [
        True, True, False, False]


def test_open_mode_admits_everything(dead_redis, monkeypatch):
    monkeypatch.setattr(rate_limit.settings, "rate_limit_degraded_mode", "open")
    assert all(check_tool_rate_limit("search", "c", 1) for _ in range(5))


def test_closed_mode_rejects_everything(dead_redis, monkeypatch):
    monkeypatch.setattr(rate_limit.settings, "rate_limit_degraded_mode", "closed")
    assert check_tool_rate_limit("search", "c", 100) is False
