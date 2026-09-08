"""Router: resolution, fallback safety, guardrail wiring, usage recording."""
import pytest

from app.middleware.cost_tracker import get_route_stats
from app.providers.base import BaseProvider
from app.router import llm_router
from app.router.llm_router import RateLimitError, RoutingError, route_request


@pytest.fixture(autouse=True)
def _no_observability(monkeypatch):
    monkeypatch.setattr(llm_router, "get_observability_configs", lambda: [])
    monkeypatch.setattr(llm_router, "get_all_guardrails_for_route", lambda r: [])


def install_routes(monkeypatch, routes):
    table = {r["name"]: r for r in routes}
    monkeypatch.setattr(llm_router, "get_route", lambda name: table.get(name))
    return table


def reply(text="hello", prompt_tokens=10, completion_tokens=5):
    return {
        "id": "chatcmpl-x",
        "object": "chat.completion",
        "model": "llama3.2:1b",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                  "total_tokens": prompt_tokens + completion_tokens},
    }


def stub_provider(monkeypatch, behaviour):
    """Install a provider whose completion is decided by `behaviour(model)`.

    behaviour returns a result dict, or an Exception instance to raise.
    """
    class _Stub(BaseProvider):
        name = "ollama"

        async def chat_completion(self, req):
            outcome = behaviour(req.model)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        async def stream_chat_completion(self, req):
            yield {}

    stub = _Stub()
    monkeypatch.setattr(llm_router, "get_provider", lambda name: stub)
    return stub


MESSAGES = [{"role": "user", "content": "hello"}]


@pytest.mark.asyncio
async def test_unknown_route_is_rejected(monkeypatch):
    install_routes(monkeypatch, [])
    with pytest.raises(RoutingError, match="not found"):
        await route_request("ghost", MESSAGES)


@pytest.mark.asyncio
async def test_successful_call_returns_content_and_records_usage(monkeypatch, route):
    install_routes(monkeypatch, [route("solo")])
    stub_provider(monkeypatch, lambda m: reply("hi there"))

    result = await route_request("solo", MESSAGES)

    assert result["choices"][0]["message"]["content"] == "hi there"
    assert result["_route"] == "solo"
    assert get_route_stats("solo")["total_requests"] == 1
    assert get_route_stats("solo")["total_tokens"] == 15


@pytest.mark.asyncio
async def test_rate_limit_raises_before_the_provider_is_called(monkeypatch, route):
    install_routes(monkeypatch, [route("tight", rate_limit_rpm=1)])
    stub_provider(monkeypatch, lambda m: reply())

    await route_request("tight", MESSAGES)
    with pytest.raises(RateLimitError) as exc:
        await route_request("tight", MESSAGES)
    assert exc.value.retry_after_seconds >= 1


@pytest.mark.asyncio
async def test_input_guardrail_block_surfaces_as_routing_error(monkeypatch, route):
    install_routes(monkeypatch, [route("guarded")])
    stub_provider(monkeypatch, lambda m: reply())
    monkeypatch.setattr(llm_router, "get_all_guardrails_for_route", lambda r: [
        {"id": "pii", "name": "pii", "type": "pii", "action_on_violation": "block",
         "apply_on_input": True, "apply_on_output": True, "config": {}},
    ])

    with pytest.raises(RoutingError, match="Input blocked"):
        await route_request("guarded", [{"role": "user", "content": "ssn 123-45-6789"}])


@pytest.mark.asyncio
async def test_unsupported_provider_is_reported_clearly(monkeypatch, route):
    bad = route("weird")
    bad["provider"] = "not-a-real-provider"
    install_routes(monkeypatch, [bad])
    with pytest.raises(RoutingError, match="Unknown provider"):
        await route_request("weird", MESSAGES)


@pytest.mark.asyncio
async def test_failing_primary_falls_back_to_its_secondary(monkeypatch, route):
    install_routes(monkeypatch, [route("primary", fallback="backup"), route("backup")])

    calls = {"n": 0}

    def behaviour(model):
        calls["n"] += 1
        if calls["n"] == 1:
            return RuntimeError("primary down")
        return reply("from backup")

    stub_provider(monkeypatch, behaviour)

    result = await route_request("primary", MESSAGES)
    assert result["choices"][0]["message"]["content"] == "from backup"


@pytest.mark.asyncio
async def test_mutually_referencing_fallbacks_are_detected(monkeypatch, route):
    """Regression: `a -> b -> a` recursed with no guard until the stack blew."""
    install_routes(monkeypatch, [
        route("a", fallback="b"),
        route("b", fallback="a"),
    ])
    stub_provider(monkeypatch, lambda m: RuntimeError("everything is down"))

    with pytest.raises(RoutingError, match="cycle|exceeded"):
        await route_request("a", MESSAGES)


@pytest.mark.asyncio
async def test_long_fallback_chain_stops_at_the_depth_limit(monkeypatch, route):
    install_routes(monkeypatch, [
        route("a", fallback="b"), route("b", fallback="c"),
        route("c", fallback="d"), route("d", fallback="e"), route("e"),
    ])
    stub_provider(monkeypatch, lambda m: RuntimeError("everything is down"))

    with pytest.raises(RoutingError, match="exceeded|cycle"):
        await route_request("a", MESSAGES)


@pytest.mark.asyncio
async def test_route_without_fallback_propagates_the_provider_error(monkeypatch, route):
    install_routes(monkeypatch, [route("lonely")])
    stub_provider(monkeypatch, lambda m: RuntimeError("boom"))

    with pytest.raises(RoutingError, match="Provider call failed"):
        await route_request("lonely", MESSAGES)


@pytest.mark.asyncio
async def test_missing_provider_usage_is_estimated_not_dropped(monkeypatch, route):
    install_routes(monkeypatch, [route("est")])
    stub_provider(monkeypatch, lambda m: reply("a reply", prompt_tokens=0, completion_tokens=0))

    result = await route_request("est", MESSAGES)
    assert result["usage"]["total_tokens"] > 0
    assert get_route_stats("est")["total_tokens"] > 0


# ── provider error handling ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_non_retryable_provider_error_does_not_burn_the_fallback(
    monkeypatch, route
):
    """A 400 or a bad key fails identically on the fallback route. Trying it
    doubles the latency of an error the caller has to fix anyway."""
    from app.providers.base import ProviderBadRequestError

    install_routes(monkeypatch, [route("primary", fallback="backup"), route("backup")])
    calls = {"n": 0}

    def behaviour(model):
        calls["n"] += 1
        return ProviderBadRequestError("model does not exist", provider="ollama",
                                       status_code=400)

    stub_provider(monkeypatch, behaviour)

    with pytest.raises(RoutingError, match="model does not exist"):
        await route_request("primary", MESSAGES)
    assert calls["n"] == 1, "the fallback route should not have been attempted"


@pytest.mark.asyncio
async def test_a_retryable_provider_error_does_fall_back(monkeypatch, route):
    from app.providers.base import ProviderOverloadedError

    install_routes(monkeypatch, [route("primary", fallback="backup"), route("backup")])
    calls = {"n": 0}

    def behaviour(model):
        calls["n"] += 1
        if calls["n"] == 1:
            return ProviderOverloadedError("upstream busy", provider="ollama",
                                           status_code=503)
        return reply("from backup")

    stub_provider(monkeypatch, behaviour)

    result = await route_request("primary", MESSAGES)
    assert result["choices"][0]["message"]["content"] == "from backup"
    assert calls["n"] > 1


@pytest.mark.asyncio
async def test_route_credentials_are_resolved_from_the_environment(monkeypatch, route):
    """The key reaches the provider without ever being stored in route config."""
    from app.router.llm_router import build_provider_request

    monkeypatch.setenv("GROQ_API_KEY", "gsk_from_env")
    r = route("groq")
    r["provider"] = "openai_compatible"
    r["api_key_env"] = "GROQ_API_KEY"

    _provider, req = build_provider_request(r, MESSAGES)
    assert req.api_key == "gsk_from_env"
    assert "gsk_from_env" not in str(r), "the secret must not land in route config"
