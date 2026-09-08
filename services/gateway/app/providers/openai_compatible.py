"""Any endpoint that speaks the OpenAI Chat Completions wire format.

One adapter, many backends. Point `base_url` at whichever you want:

    OpenAI      https://api.openai.com/v1              OPENAI_API_KEY
    Groq        https://api.groq.com/openai/v1         GROQ_API_KEY
    Together    https://api.together.xyz/v1            TOGETHER_API_KEY
    Fireworks   https://api.fireworks.ai/inference/v1  FIREWORKS_API_KEY
    DeepInfra   https://api.deepinfra.com/v1/openai    DEEPINFRA_API_KEY
    vLLM        http://vllm:8000/v1                    (no key)

Because the upstream already returns the shape the gateway serves, the work here
is not translation but SSE framing, error classification, and being careful
about `usage` — several of these servers omit it unless asked.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, AsyncIterator, Dict, Optional

import httpx

from app.providers import register
from app.providers.base import (
    BaseProvider,
    ProviderRequest,
    classify_http_error,
    wrap_transport_error,
)

log = logging.getLogger(__name__)


@register
class OpenAICompatibleProvider(BaseProvider):
    name = "openai_compatible"
    default_base_url = "https://api.openai.com/v1"
    api_key_env = "OPENAI_API_KEY"
    # False, not True: this same adapter serves a local vLLM or LM Studio that
    # wants no credential at all. A route that names an api_key_env still fails
    # loudly when that variable is missing — see credentials.resolve_api_key.
    requires_api_key = False

    def _headers(self, req: ProviderRequest) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if req.api_key:
            headers["Authorization"] = f"Bearer {req.api_key}"
        return headers

    def _payload(self, req: ProviderRequest, stream: bool) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": req.model,
            "messages": req.messages,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "stream": stream,
        }
        if req.stop:
            payload["stop"] = req.stop
        if stream and req.extra.get("include_usage", True):
            # Without this most servers send no usage on a streamed response,
            # and the gateway would fall back to estimating every token.
            payload["stream_options"] = {"include_usage": True}
        for key, value in req.extra.items():
            if key not in ("include_usage", "options"):
                payload[key] = value
        return payload

    async def chat_completion(self, req: ProviderRequest) -> Dict[str, Any]:
        url = f"{self.resolve_base_url(req)}/chat/completions"
        try:
            async with self._client(req) as client:
                resp = await client.post(
                    url, json=self._payload(req, stream=False), headers=self._headers(req)
                )
                if not resp.is_success:
                    raise classify_http_error(
                        resp.status_code, self.name, resp.text, resp.headers
                    )
                data = resp.json()
        except httpx.HTTPError as exc:
            raise wrap_transport_error(exc, self.name) from exc

        return self._normalise(data, req)

    async def stream_chat_completion(
        self, req: ProviderRequest
    ) -> AsyncIterator[Dict[str, Any]]:
        url = f"{self.resolve_base_url(req)}/chat/completions"
        try:
            async with self._client(req) as client:
                async with client.stream(
                    "POST",
                    url,
                    json=self._payload(req, stream=True),
                    headers=self._headers(req),
                ) as resp:
                    if not resp.is_success:
                        body = (await resp.aread()).decode("utf-8", "replace")
                        raise classify_http_error(
                            resp.status_code, self.name, body, resp.headers
                        )

                    async for raw in resp.aiter_lines():
                        chunk = _parse_sse_line(raw)
                        if chunk is _DONE:
                            return
                        if chunk is None:
                            continue

                        # A usage-only trailer arrives with choices: []. Hand it
                        # up as _usage; the router harvests and skips it.
                        usage = chunk.pop("usage", None)
                        if usage:
                            chunk["_usage"] = {
                                "prompt_tokens": usage.get("prompt_tokens") or 0,
                                "completion_tokens": usage.get("completion_tokens") or 0,
                            }
                        chunk.setdefault("model", req.model)
                        yield chunk
        except httpx.HTTPError as exc:
            raise wrap_transport_error(exc, self.name) from exc

    # ── internals ────────────────────────────────────────────────────────────

    def _normalise(self, data: Dict[str, Any], req: ProviderRequest) -> Dict[str, Any]:
        """Fill in the fields servers most often leave out.

        Groq, Together and friends are close to OpenAI but not identical: some
        omit `created`, some return `usage: null`. Normalising here means the
        router never has to reason about which backend it was talking to.
        """
        choices = data.get("choices") or []
        if not choices:
            raise classify_http_error(
                502, self.name, "upstream returned no choices in a completion"
            )

        choice = choices[0]
        message = choice.get("message") or {}
        usage = data.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens") or 0
        completion_tokens = usage.get("completion_tokens") or 0

        return {
            "id": data.get("id") or f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": data.get("created") or int(time.time()),
            "model": data.get("model") or req.model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": message.get("role", "assistant"),
                        "content": message.get("content") or "",
                    },
                    "finish_reason": choice.get("finish_reason") or "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": usage.get("total_tokens")
                or (prompt_tokens + completion_tokens),
            },
        }


class _Done:
    """Sentinel for the `data: [DONE]` terminator."""


_DONE = _Done()


def _parse_sse_line(raw: str) -> Optional[Any]:
    """Return a parsed chunk, `_DONE`, or None for lines to ignore.

    SSE carries blank lines between events, `:` comment lines used as keep-alives,
    and `event:` lines this API does not use. Only `data:` payloads matter.
    """
    line = raw.strip()
    if not line or line.startswith(":") or not line.startswith("data:"):
        return None

    payload = line[len("data:"):].strip()
    if payload == "[DONE]":
        return _DONE
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        log.warning("openai_compatible.bad_sse_payload payload=%r", payload[:200])
        return None
