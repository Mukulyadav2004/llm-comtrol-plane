"""Ollama provider — non-streaming completions and true incremental streaming.

Two entry points:

  * ``chat_completion``  -> one OpenAI-shaped ``chat.completion`` dict.
  * ``stream_chat_completion`` -> async iterator of OpenAI-shaped
    ``chat.completion.chunk`` dicts, yielded as they arrive.

The old implementation accepted ``stream=True``, consumed the whole Ollama
stream into a list, and returned a single blob — so nothing downstream ever
streamed, and it reported ``len(content_parts)`` (a count of *chunks*) as
``completion_tokens``. Ollama sends real counts on its final frame; those are
carried through here instead.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

log = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://ollama:11434"
_DEFAULT_TIMEOUT = 120.0


def _completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def _finish_reason(data: Dict[str, Any]) -> str:
    reason = data.get("done_reason")
    if reason == "length":
        return "length"
    if reason == "stop" or data.get("done"):
        return "stop"
    return "length"


async def chat_completion(
    base_url: str,
    model: str,
    messages: List[Dict[str, str]],
    max_tokens: int,
    temperature: float,
    stream: bool = False,
    timeout: float = _DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Non-streaming completion. `stream` is accepted for signature compatibility
    but ignored — streaming callers use `stream_chat_completion`."""
    url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
    payload = {
        "model": model,
        "messages": messages,
        "options": {"num_predict": max_tokens, "temperature": temperature},
        "stream": False,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{url}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()

    return _normalise(data, model)


async def stream_chat_completion(
    base_url: str,
    model: str,
    messages: List[Dict[str, str]],
    max_tokens: int,
    temperature: float,
    timeout: float = _DEFAULT_TIMEOUT,
) -> AsyncIterator[Dict[str, Any]]:
    """Yield OpenAI-compatible `chat.completion.chunk` dicts as they arrive.

    The final chunk carries `finish_reason` and a non-standard `_usage` key with
    Ollama's real token counts, which the router strips before emitting and uses
    for cost attribution.
    """
    url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
    payload = {
        "model": model,
        "messages": messages,
        "options": {"num_predict": max_tokens, "temperature": temperature},
        "stream": True,
    }

    completion_id = _completion_id()
    created = int(time.time())
    first = True

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", f"{url}/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    frame = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("ollama.bad_stream_frame line=%r", line[:200])
                    continue

                delta_text = (frame.get("message") or {}).get("content", "")
                done = bool(frame.get("done"))

                if delta_text or first:
                    delta: Dict[str, Any] = {"content": delta_text}
                    if first:
                        # OpenAI sends the role on the first chunk only.
                        delta = {"role": "assistant", "content": delta_text}
                        first = False
                    yield {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                    }

                if done:
                    yield {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {},
                                     "finish_reason": _finish_reason(frame)}],
                        "_usage": {
                            "prompt_tokens": frame.get("prompt_eval_count") or 0,
                            "completion_tokens": frame.get("eval_count") or 0,
                        },
                    }
                    return


def _normalise(data: Dict[str, Any], model: str) -> Dict[str, Any]:
    """Normalise an Ollama response to the OpenAI chat.completion shape."""
    message = data.get("message", {}) or {}
    prompt_tokens = data.get("prompt_eval_count") or 0
    completion_tokens = data.get("eval_count") or 0
    return {
        "id": _completion_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
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
