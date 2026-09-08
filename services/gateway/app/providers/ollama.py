"""Ollama — a local model server with its own wire format.

Ollama streams newline-delimited JSON rather than SSE, and reports token counts
only on the final frame. Both are normalised here so nothing above this file
knows the difference.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, AsyncIterator, Dict

import httpx

from app.providers import register
from app.providers.base import (
    BaseProvider,
    ProviderRequest,
    classify_http_error,
    wrap_transport_error,
)

log = logging.getLogger(__name__)


def _completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def _finish_reason(frame: Dict[str, Any]) -> str:
    if frame.get("done_reason") == "length":
        return "length"
    return "stop" if frame.get("done") else "length"


@register
class OllamaProvider(BaseProvider):
    name = "ollama"
    default_base_url = "http://ollama:11434"
    requires_api_key = False

    def _payload(self, req: ProviderRequest, stream: bool) -> Dict[str, Any]:
        options: Dict[str, Any] = {
            "num_predict": req.max_tokens,
            "temperature": req.temperature,
        }
        if req.stop:
            options["stop"] = req.stop
        options.update(req.extra.get("options", {}))
        return {
            "model": req.model,
            "messages": req.messages,
            "options": options,
            "stream": stream,
        }

    async def chat_completion(self, req: ProviderRequest) -> Dict[str, Any]:
        url = f"{self.resolve_base_url(req)}/api/chat"
        try:
            async with self._client(req) as client:
                resp = await client.post(url, json=self._payload(req, stream=False))
                if not resp.is_success:
                    raise classify_http_error(
                        resp.status_code, self.name, resp.text, resp.headers
                    )
                data = resp.json()
        except httpx.HTTPError as exc:
            raise wrap_transport_error(exc, self.name) from exc

        message = data.get("message", {}) or {}
        prompt_tokens = data.get("prompt_eval_count") or 0
        completion_tokens = data.get("eval_count") or 0
        return {
            "id": _completion_id(),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": req.model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": message.get("role", "assistant"),
                        "content": message.get("content", ""),
                    },
                    "finish_reason": _finish_reason(data),
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }

    async def stream_chat_completion(
        self, req: ProviderRequest
    ) -> AsyncIterator[Dict[str, Any]]:
        url = f"{self.resolve_base_url(req)}/api/chat"
        completion_id = _completion_id()
        created = int(time.time())
        first = True

        try:
            async with self._client(req) as client:
                async with client.stream(
                    "POST", url, json=self._payload(req, stream=True)
                ) as resp:
                    if not resp.is_success:
                        body = (await resp.aread()).decode("utf-8", "replace")
                        raise classify_http_error(
                            resp.status_code, self.name, body, resp.headers
                        )

                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            frame = json.loads(line)
                        except json.JSONDecodeError:
                            log.warning("ollama.bad_stream_frame line=%r", line[:200])
                            continue

                        text = (frame.get("message") or {}).get("content", "")
                        done = bool(frame.get("done"))

                        if text or first:
                            delta: Dict[str, Any] = (
                                {"role": "assistant", "content": text}
                                if first
                                else {"content": text}
                            )
                            first = False
                            yield {
                                "id": completion_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": req.model,
                                "choices": [
                                    {"index": 0, "delta": delta, "finish_reason": None}
                                ],
                            }

                        if done:
                            yield {
                                "id": completion_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": req.model,
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {},
                                        "finish_reason": _finish_reason(frame),
                                    }
                                ],
                                "_usage": {
                                    "prompt_tokens": frame.get("prompt_eval_count") or 0,
                                    "completion_tokens": frame.get("eval_count") or 0,
                                },
                            }
                            return
        except httpx.HTTPError as exc:
            raise wrap_transport_error(exc, self.name) from exc
