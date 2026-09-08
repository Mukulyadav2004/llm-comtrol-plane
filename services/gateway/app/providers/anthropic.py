"""Anthropic Messages API.

    ┌─────────────────────────────────────────────────────────────────────┐
    │  This adapter is intentionally unimplemented.                       │
    │                                                                     │
    │  `tests/test_anthropic_provider.py` specifies the whole contract.    │
    │  Run it, watch it fail, make it pass:                               │
    │                                                                     │
    │      pytest tests/test_anthropic_provider.py -v                     │
    │                                                                     │
    │  Anthropic is the useful one to hand-write because it is the only   │
    │  backend here that genuinely fights the abstraction. If the         │
    │  interface survives it, the interface is real.                      │
    └─────────────────────────────────────────────────────────────────────┘

What differs from the OpenAI format
-----------------------------------

**Endpoint** `POST {base_url}/messages` — not `/chat/completions`.

**Auth headers.** Not `Authorization: Bearer`:

    x-api-key: <key>
    anthropic-version: 2023-06-01        <- required; omitting it is a 400

**The system prompt is not a message.** OpenAI puts it in `messages` with
`role: "system"`. Anthropic takes a top-level `system` string, and rejects a
message with that role. `ProviderRequest.system_prompt()` and
`ProviderRequest.conversation()` exist for exactly this split.

**`max_tokens` is required.** OpenAI treats it as optional.

**Content is a list of blocks, not a string:**

    {"content": [{"type": "text", "text": "Hello"}], ...}

so the text is `content[0]["text"]`, and a response can legitimately contain
zero text blocks.

**Stop reasons differ** and must be mapped onto OpenAI's vocabulary:

    end_turn, stop_sequence  ->  "stop"
    max_tokens               ->  "length"

**Usage is named differently:** `usage.input_tokens` / `usage.output_tokens`,
not `prompt_tokens` / `completion_tokens`.

**529 means overloaded.** `classify_http_error` already handles it; you get that
for free by routing errors through it.

**Streaming is a typed event stream,** not one chunk shape repeated. The events
you must handle:

    message_start        {"message": {"usage": {"input_tokens": 9, ...}}}
                         input_tokens arrive HERE, at the very beginning
    content_block_start  {"content_block": {"type": "text", "text": ""}}
    content_block_delta  {"delta": {"type": "text_delta", "text": "Hel"}}
                         the actual text
    content_block_stop
    message_delta        {"delta": {"stop_reason": "end_turn"},
                          "usage": {"output_tokens": 12}}
                         stop_reason and output_tokens arrive HERE
    message_stop
    ping                 keep-alive; ignore it

Note that token counts are split across the first and second-to-last events, so
you have to carry `input_tokens` from `message_start` all the way through. This
is why `BaseProvider.stream_chat_completion` permits a usage-only chunk with
empty `choices` — emit one at the end and the router will harvest it.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Dict

from app.providers import register
from app.providers.base import BaseProvider, ProviderRequest

ANTHROPIC_VERSION = "2023-06-01"

#: Anthropic stop_reason -> OpenAI finish_reason.
STOP_REASON_MAP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
}

_TODO = (
    "AnthropicProvider is not implemented yet. "
    "Run `pytest tests/test_anthropic_provider.py -v` — those tests are the spec."
)


@register
class AnthropicProvider(BaseProvider):
    name = "anthropic"
    default_base_url = "https://api.anthropic.com/v1"
    api_key_env = "ANTHROPIC_API_KEY"
    requires_api_key = True

    def headers(self, req: ProviderRequest) -> Dict[str, str]:
        """Build the request headers.

        TODO: x-api-key, anthropic-version, content-type.
        `test_headers_use_x_api_key_not_bearer` and
        `test_headers_include_the_anthropic_version` cover this.
        """
        raise NotImplementedError(_TODO)

    def payload(self, req: ProviderRequest, stream: bool) -> Dict[str, Any]:
        """Build the request body.

        TODO: model, messages (system messages removed), max_tokens (required),
        temperature, stream, and `system` as a TOP-LEVEL key when there is one.
        Use req.system_prompt() and req.conversation().
        """
        raise NotImplementedError(_TODO)

    async def chat_completion(self, req: ProviderRequest) -> Dict[str, Any]:
        """POST /messages and return an OpenAI-shaped chat.completion.

        TODO: send it, route failures through classify_http_error and
        wrap_transport_error the way the other two adapters do, then map the
        response — content blocks to a string, stop_reason via STOP_REASON_MAP,
        input_tokens/output_tokens to prompt_tokens/completion_tokens.
        """
        raise NotImplementedError(_TODO)

    async def stream_chat_completion(
        self, req: ProviderRequest
    ) -> AsyncIterator[Dict[str, Any]]:
        """Translate Anthropic's typed SSE events into OpenAI chunks.

        TODO: parse `event:`/`data:` pairs, hold input_tokens from message_start,
        emit a chunk per content_block_delta (role on the first one), emit a
        finish chunk when message_delta carries a stop_reason, and finish with a
        usage-only chunk (`choices: []`, `_usage: {...}`) carrying both counts.
        """
        raise NotImplementedError(_TODO)
        yield {}  # unreachable; marks this as an async generator for the tests
