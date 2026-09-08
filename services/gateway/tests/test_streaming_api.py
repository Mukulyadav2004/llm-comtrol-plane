"""End-to-end SSE behaviour through the FastAPI app."""
import json

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.router import llm_router


@pytest.fixture(autouse=True)
def _quiet_observability(monkeypatch):
    monkeypatch.setattr(llm_router, "get_observability_configs", lambda: [])
    monkeypatch.setattr(llm_router, "get_all_guardrails_for_route", lambda r: [])


@pytest.fixture
def client():
    return TestClient(app_main.app)


@pytest.fixture
def streaming_route(monkeypatch, route):
    r = route("stream-route")
    monkeypatch.setattr(llm_router, "get_route", lambda name: r if name == r["name"] else None)
    return r


def install_stream(monkeypatch, deltas, prompt_tokens=9, completion_tokens=4):
    async def _fn(base_url, model, messages, max_tokens, temperature):
        first = True
        for d in deltas:
            delta = {"role": "assistant", "content": d} if first else {"content": d}
            first = False
            yield {"id": "chatcmpl-t", "object": "chat.completion.chunk", "created": 1,
                   "model": model,
                   "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
        yield {"id": "chatcmpl-t", "object": "chat.completion.chunk", "created": 1,
               "model": model,
               "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
               "_usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}}
    monkeypatch.setitem(llm_router._STREAM_PROVIDER_MAP, "ollama", _fn)


def sse_payloads(body: str):
    out = []
    for line in body.splitlines():
        if line.startswith("data: "):
            out.append(line[len("data: "):])
    return out


def post_stream(client, **extra):
    payload = {"model": "stream-route", "messages": [{"role": "user", "content": "hi"}],
               "stream": True}
    payload.update(extra)
    return client.post("/v1/chat/completions", json=payload)


def test_streaming_response_uses_event_stream_content_type(client, monkeypatch, streaming_route):
    install_stream(monkeypatch, ["Hello", " world"])
    resp = post_stream(client)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")


def test_stream_terminates_with_the_done_sentinel(client, monkeypatch, streaming_route):
    install_stream(monkeypatch, ["Hello"])
    payloads = sse_payloads(post_stream(client).text)
    assert payloads[-1] == "[DONE]"


def test_every_frame_is_a_chat_completion_chunk(client, monkeypatch, streaming_route):
    install_stream(monkeypatch, ["a", "b", "c"])
    for raw in sse_payloads(post_stream(client).text):
        if raw == "[DONE]":
            continue
        assert json.loads(raw)["object"] == "chat.completion.chunk"


def test_deltas_reassemble_into_the_full_message(client, monkeypatch, streaming_route):
    install_stream(monkeypatch, ["The ", "quick ", "brown ", "fox"])
    text = ""
    for raw in sse_payloads(post_stream(client).text):
        if raw == "[DONE]":
            continue
        for choice in json.loads(raw).get("choices", []):
            text += choice.get("delta", {}).get("content") or ""
    assert text == "The quick brown fox"


def test_first_chunk_carries_the_assistant_role(client, monkeypatch, streaming_route):
    install_stream(monkeypatch, ["hi"])
    first = json.loads(sse_payloads(post_stream(client).text)[0])
    assert first["choices"][0]["delta"]["role"] == "assistant"


def test_final_chunk_carries_a_finish_reason(client, monkeypatch, streaming_route):
    install_stream(monkeypatch, ["hi"])
    reasons = [
        c.get("finish_reason")
        for raw in sse_payloads(post_stream(client).text) if raw != "[DONE]"
        for c in json.loads(raw).get("choices", [])
    ]
    assert "stop" in reasons


def test_usage_trailer_is_opt_in(client, monkeypatch, streaming_route):
    install_stream(monkeypatch, ["hi"])
    without = [json.loads(r) for r in sse_payloads(post_stream(client).text) if r != "[DONE]"]
    assert all("usage" not in c for c in without)

    install_stream(monkeypatch, ["hi"])
    with_usage = [
        json.loads(r)
        for r in sse_payloads(post_stream(client, stream_options={"include_usage": True}).text)
        if r != "[DONE]"
    ]
    trailers = [c for c in with_usage if c.get("usage")]
    assert len(trailers) == 1
    assert trailers[0]["usage"]["prompt_tokens"] == 9
    assert trailers[0]["usage"]["completion_tokens"] == 4


def test_streamed_usage_reflects_tokens_not_chunk_count(client, monkeypatch, streaming_route):
    """Regression: the old code reported len(content_parts) — a count of chunks —
    as completion_tokens, so every streamed request mispriced itself."""
    from app.middleware.cost_tracker import get_route_stats

    install_stream(monkeypatch, ["a"] * 12, prompt_tokens=100, completion_tokens=37)
    post_stream(client).text

    stats = get_route_stats("stream-route")
    assert stats["completion_tokens"] == 37, "must use provider counts, not chunk count"
    assert stats["prompt_tokens"] == 100


def test_rate_limit_is_a_real_429_not_a_broken_stream(client, monkeypatch, route):
    r = route("tight-stream", rate_limit_rpm=1)
    monkeypatch.setattr(llm_router, "get_route", lambda name: r if name == r["name"] else None)
    install_stream(monkeypatch, ["hi"])

    first = client.post("/v1/chat/completions", json={
        "model": "tight-stream", "messages": [{"role": "user", "content": "hi"}], "stream": True})
    assert first.status_code == 200

    second = client.post("/v1/chat/completions", json={
        "model": "tight-stream", "messages": [{"role": "user", "content": "hi"}], "stream": True})
    assert second.status_code == 429
    assert "Retry-After" in second.headers


def test_unknown_route_is_a_502_before_the_stream_opens(client, monkeypatch):
    monkeypatch.setattr(llm_router, "get_route", lambda name: None)
    resp = post_stream(client)
    assert resp.status_code == 502


def test_streamed_output_is_redacted_by_guardrails(client, monkeypatch, streaming_route):
    monkeypatch.setattr(llm_router, "get_all_guardrails_for_route", lambda r: [
        {"id": "pii", "name": "pii", "type": "pii", "action_on_violation": "redact",
         "apply_on_input": True, "apply_on_output": True, "config": {}},
    ])
    install_stream(monkeypatch, ["write to al", "ice@exa", "mple.com", " please"])

    text = ""
    for raw in sse_payloads(post_stream(client).text):
        if raw == "[DONE]":
            continue
        for choice in json.loads(raw).get("choices", []):
            text += choice.get("delta", {}).get("content") or ""

    assert "alice@example.com" not in text
    assert "[EMAIL_REDACTED]" in text


def test_non_streaming_requests_still_return_json(client, monkeypatch, streaming_route):
    async def _fn(base_url, model, messages, max_tokens, temperature):
        return {"id": "x", "object": "chat.completion", "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "plain"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
    monkeypatch.setitem(llm_router._PROVIDER_MAP, "ollama", _fn)

    resp = client.post("/v1/chat/completions", json={
        "model": "stream-route", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["choices"][0]["message"]["content"] == "plain"
