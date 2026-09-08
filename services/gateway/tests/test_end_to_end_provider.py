"""Whole chain against a non-Ollama provider.

Everything else stubs at the provider boundary. This exercises the real path —
HTTP request -> router -> registry -> OpenAI-compatible adapter -> wire bytes —
so the multi-provider claim is tested, not assumed. Only the socket is faked.
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.middleware.cost_tracker import get_route_stats
from app.providers import get_provider
from app.router import llm_router


@pytest.fixture
def client():
    return TestClient(app_main.app)


@pytest.fixture
def groq_route(monkeypatch):
    """A route provisioned exactly as the README's Groq example describes."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key")
    route = {
        "name": "groq-fast",
        "provider": "openai_compatible",
        "model": "llama-3.3-70b-versatile",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "enabled": True,
        "fallback": None,
        "guardrail_ids": [],
        "policy": {"rate_limit_rpm": 60, "max_tokens": 128, "temperature": 0.4,
                   "cost_per_1k_tokens": 0.001},
    }
    monkeypatch.setattr(llm_router, "get_route",
                        lambda name: route if name == "groq-fast" else None)
    monkeypatch.setattr(llm_router, "get_all_guardrails_for_route", lambda r: [])
    monkeypatch.setattr(llm_router, "get_observability_configs", lambda: [])
    return route


def test_a_completion_goes_out_as_groq_expects_it(client, groq_route, mock_http):
    seen = mock_http(get_provider("openai_compatible"), lambda request: httpx.Response(
        200, json={
            "id": "chatcmpl-1", "created": 1700000000,
            "model": "llama-3.3-70b-versatile",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "42"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
        }))

    resp = client.post("/v1/chat/completions", json={
        "model": "groq-fast",
        "messages": [{"role": "user", "content": "what is 6 times 7"}]})

    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "42"

    sent = seen[0]
    assert str(sent.url) == "https://api.groq.com/openai/v1/chat/completions"
    assert sent.headers["authorization"] == "Bearer gsk_test_key"
    body = json.loads(sent.content)
    assert body["model"] == "llama-3.3-70b-versatile"
    assert body["max_tokens"] == 128
    assert body["temperature"] == 0.4

    stats = get_route_stats("groq-fast")
    assert stats["total_tokens"] == 15
    assert stats["total_usd"] == pytest.approx(15 / 1000 * 0.001)


def test_a_streamed_completion_survives_the_whole_chain(client, groq_route, mock_http):
    frames = [
        {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "6"},
                      "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"content": " times 7"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"content": " is 42"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 5}},
    ]
    body = "".join(f"data: {json.dumps(f)}\n\n" for f in frames) + "data: [DONE]\n\n"
    mock_http(get_provider("openai_compatible"),
              lambda request: httpx.Response(200, text=body))

    resp = client.post("/v1/chat/completions", json={
        "model": "groq-fast", "stream": True,
        "messages": [{"role": "user", "content": "what is 6 times 7"}]})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    payloads = [l[len("data: "):] for l in resp.text.splitlines() if l.startswith("data: ")]
    assert payloads[-1] == "[DONE]"

    text = ""
    for raw in payloads[:-1]:
        for choice in json.loads(raw).get("choices", []):
            text += choice.get("delta", {}).get("content") or ""
    assert text == "6 times 7 is 42"

    # The usage trailer arrived with choices: [] and still reached cost tracking.
    assert get_route_stats("groq-fast")["completion_tokens"] == 5


def test_an_upstream_401_is_reported_as_a_gateway_error(client, groq_route, mock_http):
    mock_http(get_provider("openai_compatible"),
              lambda request: httpx.Response(401, json={"error": "invalid api key"}))
    resp = client.post("/v1/chat/completions", json={
        "model": "groq-fast", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 502
    assert "invalid api key" in resp.json()["detail"]


def test_a_route_naming_a_missing_env_var_fails_fast(client, groq_route, monkeypatch):
    """Better a clear gateway error than an unauthenticated request and a
    third-party 401 the operator has to go read logs to understand."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    resp = client.post("/v1/chat/completions", json={
        "model": "groq-fast", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 502
    assert "GROQ_API_KEY" in resp.json()["detail"]
