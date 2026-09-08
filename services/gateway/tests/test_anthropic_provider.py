"""Contract for the Anthropic adapter.

    These tests fail until app/providers/anthropic.py is implemented.
    That is deliberate — they are the specification. Work top to bottom:

        pytest tests/test_anthropic_provider.py -v -x

    The -x stops at the first failure, so each run points at exactly one thing
    to fix. Roughly the order the tests are written in:

        1. headers      x-api-key + anthropic-version
        2. payload      /messages, system hoisted out of messages, max_tokens
        3. response     content blocks -> string, stop_reason, token names
        4. errors       401 / 429 / 529
        5. streaming    the typed event stream

    The module docstring in app/providers/anthropic.py documents every way this
    API differs from the OpenAI format.
"""
import json

import httpx
import pytest

from app.providers import get_provider
from app.providers.base import (
    ProviderAuthError,
    ProviderOverloadedError,
    ProviderRateLimitError,
)


# ─────────────────────────────────────────────────────────────────────────────
# DELETE THIS BLOCK once AnthropicProvider is implemented.
#
# Until then these 28 tests are expected failures, so `main` stays green while
# the adapter is unwritten. `raises=NotImplementedError` keeps the exemption
# narrow: only "not written yet" is tolerated. The moment there is a real
# implementation with a real bug, that test fails properly instead of being
# quietly absorbed. strict=False so partial progress shows up as XPASS rather
# than breaking the build mid-way through.
pytestmark = pytest.mark.xfail(
    reason="AnthropicProvider is not implemented yet — these tests are its spec",
    raises=NotImplementedError,
    strict=False,
)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def provider():
    return get_provider("anthropic")


def message_body(blocks=None, stop_reason="end_turn", input_tokens=11, output_tokens=7):
    """A response shaped the way the Messages API actually shapes it."""
    return {
        "id": "msg_013Zva2CMHLNnXjNJJKqJ2EF",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-20250514",
        "content": blocks if blocks is not None else [{"type": "text", "text": "Hello!"}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def json_handler(body, status=200, headers=None):
    def _handler(request):
        return httpx.Response(status, json=body, headers=headers or {})
    return _handler


def event_stream(*events):
    """Anthropic sends `event:` and `data:` pairs and never sends [DONE]."""
    body = "".join(
        f"event: {name}\ndata: {json.dumps(payload)}\n\n" for name, payload in events
    )
    def _handler(request):
        return httpx.Response(200, text=body,
                              headers={"content-type": "text/event-stream"})
    return _handler


def default_events(text_parts=("Hello", " there"), stop_reason="end_turn",
                   input_tokens=25, output_tokens=15):
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "content": [],
            "model": "claude-sonnet-4-20250514", "stop_reason": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
    ]
    for part in text_parts:
        events.append(("content_block_delta", {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": part}}))
    events += [
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta",
                           "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                           "usage": {"output_tokens": output_tokens}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return events


async def collect(provider, req):
    return [c async for c in provider.stream_chat_completion(req)]


def stream_text(chunks):
    return "".join(
        c["choices"][0]["delta"].get("content") or ""
        for c in chunks if c.get("choices")
    )


# ── 1. headers ────────────────────────────────────────────────────────────────

async def test_headers_use_x_api_key_not_bearer(provider, mock_http, provider_request):
    seen = mock_http(provider, json_handler(message_body()))
    await provider.chat_completion(provider_request(api_key="sk-ant-secret"))
    assert seen[0].headers["x-api-key"] == "sk-ant-secret"
    assert "authorization" not in seen[0].headers


async def test_headers_include_the_anthropic_version(provider, mock_http, provider_request):
    """Omitting anthropic-version is a 400, not a default."""
    seen = mock_http(provider, json_handler(message_body()))
    await provider.chat_completion(provider_request(api_key="k"))
    assert seen[0].headers["anthropic-version"] == "2023-06-01"


# ── 2. payload ────────────────────────────────────────────────────────────────

async def test_request_goes_to_the_messages_endpoint(provider, mock_http, provider_request):
    seen = mock_http(provider, json_handler(message_body()))
    await provider.chat_completion(
        provider_request(api_key="k", base_url="https://api.anthropic.com/v1"))
    assert str(seen[0].url) == "https://api.anthropic.com/v1/messages"


async def test_max_tokens_is_always_sent(provider, mock_http, provider_request):
    """Optional for OpenAI, required here."""
    seen = mock_http(provider, json_handler(message_body()))
    await provider.chat_completion(provider_request(api_key="k", max_tokens=512))
    assert json.loads(seen[0].content)["max_tokens"] == 512


async def test_system_prompt_is_hoisted_out_of_messages(provider, mock_http, provider_request):
    """The API rejects a message with role='system'. It belongs at the top level."""
    seen = mock_http(provider, json_handler(message_body()))
    await provider.chat_completion(provider_request(api_key="k", messages=[
        {"role": "system", "content": "You are terse."},
        {"role": "user", "content": "hi"},
    ]))
    body = json.loads(seen[0].content)
    assert body["system"] == "You are terse."
    assert [m["role"] for m in body["messages"]] == ["user"]


async def test_no_system_key_when_there_is_no_system_message(provider, mock_http, provider_request):
    seen = mock_http(provider, json_handler(message_body()))
    await provider.chat_completion(provider_request(api_key="k"))
    assert "system" not in json.loads(seen[0].content)


async def test_multiple_system_messages_are_combined(provider, mock_http, provider_request):
    seen = mock_http(provider, json_handler(message_body()))
    await provider.chat_completion(provider_request(api_key="k", messages=[
        {"role": "system", "content": "Be terse."},
        {"role": "system", "content": "Be kind."},
        {"role": "user", "content": "hi"},
    ]))
    body = json.loads(seen[0].content)
    assert "Be terse." in body["system"] and "Be kind." in body["system"]


async def test_sampling_parameters_are_forwarded(provider, mock_http, provider_request):
    seen = mock_http(provider, json_handler(message_body()))
    await provider.chat_completion(
        provider_request(api_key="k", model="claude-sonnet-4-20250514", temperature=0.2))
    body = json.loads(seen[0].content)
    assert body["model"] == "claude-sonnet-4-20250514"
    assert body["temperature"] == 0.2


# ── 3. response mapping ───────────────────────────────────────────────────────

async def test_content_blocks_become_a_string(provider, mock_http, provider_request):
    mock_http(provider, json_handler(
        message_body([{"type": "text", "text": "Hello!"}])))
    result = await provider.chat_completion(provider_request(api_key="k"))
    assert result["choices"][0]["message"]["content"] == "Hello!"


async def test_multiple_text_blocks_are_concatenated(provider, mock_http, provider_request):
    mock_http(provider, json_handler(message_body([
        {"type": "text", "text": "Hello"},
        {"type": "text", "text": " world"},
    ])))
    result = await provider.chat_completion(provider_request(api_key="k"))
    assert result["choices"][0]["message"]["content"] == "Hello world"


async def test_a_response_with_no_text_blocks_yields_empty_content(
    provider, mock_http, provider_request
):
    """Legal, and it must not raise — an empty answer is not a crash."""
    mock_http(provider, json_handler(message_body([])))
    result = await provider.chat_completion(provider_request(api_key="k"))
    assert result["choices"][0]["message"]["content"] == ""


async def test_non_text_blocks_are_skipped(provider, mock_http, provider_request):
    mock_http(provider, json_handler(message_body([
        {"type": "thinking", "thinking": "hmm"},
        {"type": "text", "text": "the answer"},
    ])))
    result = await provider.chat_completion(provider_request(api_key="k"))
    assert result["choices"][0]["message"]["content"] == "the answer"


@pytest.mark.parametrize("stop_reason,expected", [
    ("end_turn", "stop"),
    ("stop_sequence", "stop"),
    ("max_tokens", "length"),
])
async def test_stop_reason_maps_to_openai_finish_reason(
    provider, mock_http, provider_request, stop_reason, expected
):
    mock_http(provider, json_handler(message_body(stop_reason=stop_reason)))
    result = await provider.chat_completion(provider_request(api_key="k"))
    assert result["choices"][0]["finish_reason"] == expected


async def test_token_counts_are_renamed(provider, mock_http, provider_request):
    """input_tokens/output_tokens here, prompt_tokens/completion_tokens upstream."""
    mock_http(provider, json_handler(message_body(input_tokens=31, output_tokens=9)))
    result = await provider.chat_completion(provider_request(api_key="k"))
    assert result["usage"] == {
        "prompt_tokens": 31, "completion_tokens": 9, "total_tokens": 40}


async def test_the_result_is_an_openai_chat_completion(provider, mock_http, provider_request):
    mock_http(provider, json_handler(message_body()))
    result = await provider.chat_completion(provider_request(api_key="k"))
    assert result["object"] == "chat.completion"
    assert result["choices"][0]["index"] == 0
    assert result["choices"][0]["message"]["role"] == "assistant"
    assert isinstance(result["created"], int)
    assert result["id"]


# ── 4. errors ─────────────────────────────────────────────────────────────────

async def test_401_becomes_an_auth_error(provider, mock_http, provider_request):
    mock_http(provider, json_handler(
        {"error": {"type": "authentication_error"}}, status=401))
    with pytest.raises(ProviderAuthError):
        await provider.chat_completion(provider_request(api_key="bad"))


async def test_429_becomes_a_retryable_rate_limit_error(provider, mock_http, provider_request):
    mock_http(provider, json_handler({"error": {}}, status=429,
                                     headers={"retry-after": "8"}))
    with pytest.raises(ProviderRateLimitError) as exc:
        await provider.chat_completion(provider_request(api_key="k"))
    assert exc.value.retry_after == 8.0


async def test_529_is_treated_as_overloaded_and_retryable(provider, mock_http, provider_request):
    """529 is Anthropic-specific. Classified as a client error it would look
    permanent, and no fallback would ever fire."""
    mock_http(provider, json_handler({"error": {"type": "overloaded_error"}}, status=529))
    with pytest.raises(ProviderOverloadedError) as exc:
        await provider.chat_completion(provider_request(api_key="k"))
    assert exc.value.retryable is True


# ── 5. streaming ──────────────────────────────────────────────────────────────

async def test_text_deltas_reassemble_into_the_message(provider, mock_http, provider_request):
    mock_http(provider, event_stream(*default_events(("Hello", " there"))))
    chunks = await collect(provider, provider_request(api_key="k"))
    assert stream_text(chunks) == "Hello there"


async def test_the_first_content_chunk_carries_the_assistant_role(
    provider, mock_http, provider_request
):
    mock_http(provider, event_stream(*default_events()))
    chunks = await collect(provider, provider_request(api_key="k"))
    content_chunks = [c for c in chunks if c.get("choices")
                      and "content" in c["choices"][0]["delta"]]
    assert content_chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    assert all("role" not in c["choices"][0]["delta"] for c in content_chunks[1:])


async def test_every_streamed_frame_is_a_chat_completion_chunk(
    provider, mock_http, provider_request
):
    mock_http(provider, event_stream(*default_events()))
    for c in await collect(provider, provider_request(api_key="k")):
        assert c["object"] == "chat.completion.chunk"


async def test_exactly_one_chunk_carries_a_finish_reason(provider, mock_http, provider_request):
    mock_http(provider, event_stream(*default_events(stop_reason="max_tokens")))
    chunks = await collect(provider, provider_request(api_key="k"))
    finishes = [c["choices"][0]["finish_reason"] for c in chunks
                if c.get("choices") and c["choices"][0].get("finish_reason")]
    assert finishes == ["length"]


async def test_usage_combines_both_ends_of_the_stream(provider, mock_http, provider_request):
    """input_tokens arrive on message_start, output_tokens on message_delta near
    the end. Both have to reach the router or streamed requests get mispriced."""
    mock_http(provider, event_stream(
        *default_events(input_tokens=42, output_tokens=13)))
    chunks = await collect(provider, provider_request(api_key="k"))
    usages = [c["_usage"] for c in chunks if "_usage" in c]
    assert len(usages) == 1
    assert usages[0] == {"prompt_tokens": 42, "completion_tokens": 13}


async def test_a_usage_only_chunk_carries_no_choices(provider, mock_http, provider_request):
    """BaseProvider permits this; the router harvests _usage then skips it."""
    mock_http(provider, event_stream(*default_events()))
    chunks = await collect(provider, provider_request(api_key="k"))
    for c in chunks:
        if "_usage" in c and not c.get("choices"):
            return
    pytest.fail("expected a trailing usage-only chunk with empty choices")


async def test_ping_events_are_ignored(provider, mock_http, provider_request):
    events = default_events()
    events.insert(2, ("ping", {"type": "ping"}))
    mock_http(provider, event_stream(*events))
    chunks = await collect(provider, provider_request(api_key="k"))
    assert stream_text(chunks) == "Hello there"


async def test_an_error_status_on_a_stream_raises_before_any_chunk(
    provider, mock_http, provider_request
):
    mock_http(provider, json_handler({"error": {}}, status=401))
    with pytest.raises(ProviderAuthError):
        await collect(provider, provider_request(api_key="bad"))
