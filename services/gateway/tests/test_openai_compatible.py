"""The generic OpenAI-format adapter — the one that unlocks Groq, Together,
Fireworks, DeepInfra and a local vLLM from a single implementation."""
import json

import httpx
import pytest

from app.providers import get_provider
from app.providers.base import (
    ProviderAuthError,
    ProviderOverloadedError,
    ProviderRateLimitError,
)


@pytest.fixture
def provider():
    return get_provider("openai_compatible")


def completion_body(content="hi there", **usage):
    return {
        "id": "chatcmpl-abc",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "llama-3.3-70b-versatile",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": usage or {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


def json_handler(body, status=200, headers=None):
    def _handler(request):
        return httpx.Response(status, json=body, headers=headers or {})
    return _handler


def sse_handler(*frames, status=200):
    body = "".join(f"data: {json.dumps(f)}\n\n" for f in frames) + "data: [DONE]\n\n"
    def _handler(request):
        return httpx.Response(status, text=body,
                              headers={"content-type": "text/event-stream"})
    return _handler


def chunk(text=None, finish=None, usage=None, role=False):
    delta = {}
    if role:
        delta["role"] = "assistant"
    if text is not None:
        delta["content"] = text
    frame = {"id": "chatcmpl-abc", "object": "chat.completion.chunk",
             "model": "llama-3.3-70b-versatile",
             "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage is not None:
        frame["choices"] = []
        frame["usage"] = usage
    return frame


# ── request shaping ───────────────────────────────────────────────────────────

async def test_request_goes_to_the_chat_completions_path(provider, mock_http, provider_request):
    seen = mock_http(provider, json_handler(completion_body()))
    await provider.chat_completion(
        provider_request(base_url="https://api.groq.com/openai/v1"))
    assert str(seen[0].url) == "https://api.groq.com/openai/v1/chat/completions"


async def test_api_key_is_sent_as_a_bearer_token(provider, mock_http, provider_request):
    seen = mock_http(provider, json_handler(completion_body()))
    await provider.chat_completion(provider_request(api_key="gsk_secret"))
    assert seen[0].headers["authorization"] == "Bearer gsk_secret"


async def test_no_authorization_header_when_there_is_no_key(provider, mock_http, provider_request):
    """A local vLLM wants no credential, and sending `Bearer None` would 401."""
    seen = mock_http(provider, json_handler(completion_body()))
    await provider.chat_completion(provider_request(api_key=None))
    assert "authorization" not in seen[0].headers


async def test_payload_carries_the_sampling_parameters(provider, mock_http, provider_request):
    seen = mock_http(provider, json_handler(completion_body()))
    await provider.chat_completion(
        provider_request(model="mixtral", max_tokens=256, temperature=0.1))
    body = json.loads(seen[0].content)
    assert body["model"] == "mixtral"
    assert body["max_tokens"] == 256
    assert body["temperature"] == 0.1
    assert body["stream"] is False


async def test_stop_sequences_are_forwarded_only_when_set(provider, mock_http, provider_request):
    seen = mock_http(provider, json_handler(completion_body()))
    await provider.chat_completion(provider_request())
    assert "stop" not in json.loads(seen[0].content)

    await provider.chat_completion(provider_request(stop=["\n\n"]))
    assert json.loads(seen[1].content)["stop"] == ["\n\n"]


async def test_extra_options_pass_through(provider, mock_http, provider_request):
    seen = mock_http(provider, json_handler(completion_body()))
    await provider.chat_completion(provider_request(extra={"top_p": 0.9, "seed": 42}))
    body = json.loads(seen[0].content)
    assert body["top_p"] == 0.9
    assert body["seed"] == 42


# ── response normalisation ────────────────────────────────────────────────────

async def test_completion_is_returned_in_openai_shape(provider, mock_http, provider_request):
    mock_http(provider, json_handler(completion_body("hello world")))
    result = await provider.chat_completion(provider_request())
    assert result["object"] == "chat.completion"
    assert result["choices"][0]["message"]["content"] == "hello world"
    assert result["choices"][0]["finish_reason"] == "stop"
    assert result["usage"]["total_tokens"] == 18


async def test_missing_created_and_usage_are_filled_in(provider, mock_http, provider_request):
    """Several OpenAI-compatible servers omit these. The router should never
    have to know which backend it was talking to."""
    mock_http(provider, json_handler({
        "id": "x",
        "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
    }))
    result = await provider.chat_completion(provider_request())
    assert isinstance(result["created"], int)
    assert result["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    assert result["choices"][0]["message"]["role"] == "assistant"


async def test_total_tokens_is_derived_when_absent(provider, mock_http, provider_request):
    mock_http(provider, json_handler(completion_body(prompt_tokens=5, completion_tokens=6)))
    result = await provider.chat_completion(provider_request())
    assert result["usage"]["total_tokens"] == 11


async def test_a_response_with_no_choices_is_an_error_not_a_crash(provider, mock_http, provider_request):
    mock_http(provider, json_handler({"id": "x", "choices": []}))
    with pytest.raises(Exception) as exc:
        await provider.chat_completion(provider_request())
    assert "choices" in str(exc.value)


# ── errors ────────────────────────────────────────────────────────────────────

async def test_401_becomes_an_auth_error(provider, mock_http, provider_request):
    mock_http(provider, json_handler({"error": "bad key"}, status=401))
    with pytest.raises(ProviderAuthError):
        await provider.chat_completion(provider_request())


async def test_429_becomes_a_retryable_rate_limit_error(provider, mock_http, provider_request):
    mock_http(provider, json_handler({"error": "slow down"}, status=429,
                                     headers={"retry-after": "12"}))
    with pytest.raises(ProviderRateLimitError) as exc:
        await provider.chat_completion(provider_request())
    assert exc.value.retry_after == 12.0
    assert exc.value.retryable is True


async def test_503_becomes_a_retryable_overload_error(provider, mock_http, provider_request):
    mock_http(provider, json_handler({"error": "busy"}, status=503))
    with pytest.raises(ProviderOverloadedError):
        await provider.chat_completion(provider_request())


async def test_a_connection_failure_is_wrapped(provider, mock_http, provider_request):
    def _boom(request):
        raise httpx.ConnectError("connection refused")
    mock_http(provider, _boom)
    from app.providers.base import ProviderConnectionError
    with pytest.raises(ProviderConnectionError):
        await provider.chat_completion(provider_request())


# ── streaming ─────────────────────────────────────────────────────────────────

async def collect(provider, req):
    return [c async for c in provider.stream_chat_completion(req)]


async def test_stream_requests_usage_by_default(provider, mock_http, provider_request):
    """Without stream_options most servers report no usage at all, and every
    streamed request would fall back to estimated token counts."""
    seen = mock_http(provider, sse_handler(chunk("hi", role=True)))
    await collect(provider, provider_request())
    body = json.loads(seen[0].content)
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}


async def test_stream_usage_can_be_turned_off_for_servers_that_reject_it(
    provider, mock_http, provider_request
):
    seen = mock_http(provider, sse_handler(chunk("hi", role=True)))
    await collect(provider, provider_request(extra={"include_usage": False}))
    assert "stream_options" not in json.loads(seen[0].content)


async def test_deltas_are_yielded_in_order(provider, mock_http, provider_request):
    mock_http(provider, sse_handler(
        chunk("The ", role=True), chunk("quick "), chunk("fox"), chunk(finish="stop")))
    chunks = await collect(provider, provider_request())
    text = "".join(
        c["choices"][0]["delta"].get("content") or ""
        for c in chunks if c.get("choices")
    )
    assert text == "The quick fox"


async def test_the_done_sentinel_ends_the_stream(provider, mock_http, provider_request):
    mock_http(provider, sse_handler(chunk("a", role=True), chunk(finish="stop")))
    chunks = await collect(provider, provider_request())
    assert all(c.get("object") == "chat.completion.chunk" for c in chunks)
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


async def test_usage_trailer_is_surfaced_as_underscore_usage(provider, mock_http, provider_request):
    mock_http(provider, sse_handler(
        chunk("a", role=True),
        chunk(finish="stop"),
        chunk(usage={"prompt_tokens": 31, "completion_tokens": 9}),
    ))
    chunks = await collect(provider, provider_request())
    trailers = [c for c in chunks if "_usage" in c]
    assert len(trailers) == 1
    assert trailers[0]["_usage"] == {"prompt_tokens": 31, "completion_tokens": 9}
    assert trailers[0]["choices"] == [], "a usage trailer carries no content"


async def test_keepalive_comments_and_blank_lines_are_ignored(provider, mock_http, provider_request):
    def _handler(request):
        body = (
            ": keep-alive\n\n"
            "\n"
            f"data: {json.dumps(chunk('hi', role=True))}\n\n"
            "event: ping\n\n"
            f"data: {json.dumps(chunk(finish='stop'))}\n\n"
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=body)
    mock_http(provider, _handler)
    chunks = await collect(provider, provider_request())
    assert len(chunks) == 2


async def test_a_malformed_frame_is_skipped_not_fatal(provider, mock_http, provider_request):
    def _handler(request):
        body = (
            f"data: {json.dumps(chunk('ok', role=True))}\n\n"
            "data: {not json at all\n\n"
            f"data: {json.dumps(chunk(finish='stop'))}\n\n"
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=body)
    mock_http(provider, _handler)
    chunks = await collect(provider, provider_request())
    assert len(chunks) == 2


async def test_an_error_status_on_a_stream_raises_before_any_chunk(
    provider, mock_http, provider_request
):
    mock_http(provider, json_handler({"error": "nope"}, status=401))
    with pytest.raises(ProviderAuthError):
        await collect(provider, provider_request())
