"""Hedged requests — fire a secondary only when the primary is slow."""
import asyncio

import pytest

from app.providers.base import ProviderBadRequestError, ProviderOverloadedError
from app.router import hedged_request
from app.router.hedged_request import hedged_call


@pytest.fixture(autouse=True)
def fast_hedge(monkeypatch):
    """Hedge after 50ms so tests do not wait a full second."""
    monkeypatch.setattr(hedged_request, "_compute_hedge_threshold", lambda route: 50)


def answer(text):
    return {"choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


def caller(behaviour, calls=None):
    async def _call(route_name, messages):
        if calls is not None:
            calls.append(route_name)
        outcome = behaviour(route_name)
        if isinstance(outcome, tuple):
            delay, outcome = outcome
            await asyncio.sleep(delay)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    return _call


async def test_a_fast_primary_never_fires_the_secondary():
    calls = []
    result = await hedged_call("primary", [], caller(lambda r: answer("fast"), calls), "backup")
    assert result["choices"][0]["message"]["content"] == "fast"
    assert result["_hedged"] is False
    assert calls == ["primary"]


async def test_a_slow_primary_fires_the_secondary():
    calls = []
    def behaviour(route):
        return (5.0, answer("slow primary")) if route == "primary" else answer("backup")
    result = await hedged_call("primary", [], caller(behaviour, calls), "backup")
    assert result["choices"][0]["message"]["content"] == "backup"
    assert result["_hedged"] is True
    assert result["_hedge_winner"] == "backup"
    assert "backup" in calls


async def test_no_hedge_route_means_a_plain_call():
    calls = []
    result = await hedged_call("primary", [], caller(lambda r: answer("only"), calls), None)
    assert result["choices"][0]["message"]["content"] == "only"
    assert calls == ["primary"]


async def test_a_permanent_primary_failure_does_not_fire_the_secondary():
    """Hedging covers a slow tail, not a broken request. A 400 fails identically
    on the secondary, so firing it spends a second upstream for the same error."""
    calls = []
    def behaviour(route):
        return ProviderBadRequestError("bad model", provider="ollama", status_code=400)

    with pytest.raises(ProviderBadRequestError):
        await hedged_call("primary", [], caller(behaviour, calls), "backup")
    assert calls == ["primary"], "the secondary should never have been called"


async def test_a_retryable_primary_failure_does_fire_the_secondary():
    calls = []
    def behaviour(route):
        if route == "primary":
            return ProviderOverloadedError("busy", provider="ollama", status_code=503)
        return answer("backup saved it")

    result = await hedged_call("primary", [], caller(behaviour, calls), "backup")
    assert result["choices"][0]["message"]["content"] == "backup saved it"
    assert calls == ["primary", "backup"]


async def test_a_secondary_that_succeeds_after_the_primary_fails_still_wins():
    """Regression: the wait loop stopped at the first task to *finish*, so a
    primary that failed quickly abandoned a healthy secondary still in flight."""
    def behaviour(route):
        if route == "primary":
            return (0.10, ProviderOverloadedError("busy", provider="o", status_code=503))
        return (0.30, answer("late but good"))

    result = await hedged_call("primary", [], caller(behaviour), "backup")
    assert result["choices"][0]["message"]["content"] == "late but good"
    assert result["_hedge_winner"] == "backup"


async def test_when_both_fail_the_primarys_error_is_the_one_raised():
    """A generic 'both failed' would discard the status code the router branches
    on to decide whether a fallback is even worth trying."""
    def behaviour(route):
        if route == "primary":
            return ProviderOverloadedError("primary is down", provider="o", status_code=503)
        return ProviderOverloadedError("backup is down", provider="o", status_code=503)

    with pytest.raises(ProviderOverloadedError, match="primary is down"):
        await hedged_call("primary", [], caller(behaviour), "backup")
