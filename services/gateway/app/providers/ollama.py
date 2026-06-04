"""Ollama provider — streams or returns completions from a local Ollama instance."""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

log = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://ollama:11434"


async def chat_completion(
    base_url: str,
    model: str,
    messages: List[Dict[str, str]],
    max_tokens: int,
    temperature: float,
    stream: bool = False,
) -> Dict[str, Any]:
    url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
    payload = {
        "model": model,
        "messages": messages,
        "options": {"num_predict": max_tokens, "temperature": temperature},
        "stream": stream,
    }

    async with httpx.AsyncClient(timeout=120) as client:
        if stream:
            return await _stream_response(client, f"{url}/api/chat", payload)
        resp = await client.post(f"{url}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()

    return _normalise(data, model)


async def _stream_response(client: httpx.AsyncClient, url: str, payload: Dict) -> Dict[str, Any]:
    content_parts = []
    async with client.stream("POST", url, json=payload) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            delta = chunk.get("message", {}).get("content", "")
            content_parts.append(delta)
            if chunk.get("done"):
                break
    return {
        "id": "ollama-stream",
        "object": "chat.completion",
        "model": payload["model"],
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "".join(content_parts)}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": len(content_parts), "total_tokens": len(content_parts)},
    }


def _normalise(data: Dict[str, Any], model: str) -> Dict[str, Any]:
    """Normalise Ollama response to OpenAI-compatible shape."""
    message = data.get("message", {})
    return {
        "id": f"ollama-{data.get('created_at', 'unknown')}",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": message.get("role", "assistant"), "content": message.get("content", "")},
                "finish_reason": "stop" if data.get("done") else "length",
            }
        ],
        "usage": {
            "prompt_tokens": data.get("prompt_eval_count", 0),
            "completion_tokens": data.get("eval_count", 0),
            "total_tokens": data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
        },
    }
