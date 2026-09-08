"""Token counting fallback.

`tiktoken` has been in requirements.txt since the first commit and was never
imported, which left the gateway with no way to fill in usage when a provider
omits it. Ollama reports `prompt_eval_count`/`eval_count` on its final streaming
frame, but not every provider does, and the streamed path previously reported
*chunk counts* as `completion_tokens` — inflating or deflating every cost figure
derived from a streamed request.

These counts are an estimate. tiktoken's cl100k_base is an OpenAI tokenizer, so
for a Llama model the number is close but not exact; it is used only when the
provider gives us nothing better.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

_ENCODING = None
_ENCODING_TRIED = False

# Rough bytes-per-token when tiktoken is unavailable.
_CHARS_PER_TOKEN = 4


def _encoding():
    global _ENCODING, _ENCODING_TRIED
    if _ENCODING_TRIED:
        return _ENCODING
    _ENCODING_TRIED = True
    try:
        import tiktoken

        _ENCODING = tiktoken.get_encoding("cl100k_base")
    except Exception:
        log.warning("token_counter.tiktoken_unavailable falling back to char heuristic")
        _ENCODING = None
    return _ENCODING


def count_tokens(text: Optional[str]) -> int:
    if not text:
        return 0
    enc = _encoding()
    if enc is None:
        return max(1, len(text) // _CHARS_PER_TOKEN)
    return len(enc.encode(text))


def count_message_tokens(messages: List[Dict[str, str]]) -> int:
    """Approximate prompt tokens, including per-message chat framing overhead."""
    if not messages:
        return 0
    # ~4 tokens of role/delimiter framing per message, ~3 priming the reply.
    total = 3
    for m in messages:
        total += 4
        total += count_tokens(m.get("content"))
        if m.get("role"):
            total += count_tokens(m["role"])
    return total


def resolve_usage(
    reported: Optional[Dict[str, int]],
    messages: List[Dict[str, str]],
    completion_text: str,
) -> Dict[str, int]:
    """Prefer provider-reported counts; estimate only what is missing.

    A provider reporting 0 completion tokens for non-empty output is treated as
    "not reported" rather than "genuinely zero" — otherwise cost silently
    collapses to zero for that request.
    """
    reported = reported or {}
    prompt = int(reported.get("prompt_tokens") or 0)
    completion = int(reported.get("completion_tokens") or 0)

    if prompt <= 0:
        prompt = count_message_tokens(messages)
    if completion <= 0 and completion_text:
        completion = count_tokens(completion_text)

    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
