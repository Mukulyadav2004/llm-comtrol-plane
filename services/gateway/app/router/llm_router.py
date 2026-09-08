"""Core routing logic.

Request lifecycle:
  1. Resolve route name  ->  explicit name OR semantic classification (route='auto')
  2. Rate limit check    ->  Redis sliding window (atomic test-and-consume)
  3. Guardrail input     ->  per-message PII / toxicity / regex / keyword filters
  4. Provider call       ->  direct OR hedged (secondary fires at primary's P90)
  5. Record latency      ->  rolling percentiles in Redis
  6. Guardrail output    ->  buffered incrementally on the streaming path
  7. Cost tracking       ->  token usage -> estimated USD in Redis

Fallback safety: a route naming another route as its fallback forms a graph the
operator can accidentally make cyclic (a -> b -> a). Every hop is recorded in
`_visited` and both cycles and excessive depth abort with a RoutingError. The
previous implementation recursed with no guard at all, so a two-route cycle
recursed until the interpreter's stack gave out.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, FrozenSet, List, Optional

from app.config import settings
from app.middleware.cost_tracker import record_usage
from app.middleware.guardrails import (
    GuardrailViolation,
    apply_input_guardrails,
    apply_output_guardrails,
    compile_stream_rules,
)
from app.middleware.latency_tracker import record_latency
from app.middleware.observability.base import LLMTrace
from app.middleware.observability.tracer import dispatch_trace
from app.middleware.rate_limit import check_rate_limit_detailed
from app.middleware.stream_guard import StreamGuard
from app.middleware.token_counter import resolve_usage
from app.providers import ollama
from app.router.classifier import classify_intent
from app.router.config_store import (
    get_all_guardrails_for_route,
    get_observability_configs,
    get_route,
    get_semantic_rules,
)
from app.router.hedged_request import hedged_call

log = logging.getLogger(__name__)

_PROVIDER_MAP = {
    "ollama": ollama.chat_completion,
}

_STREAM_PROVIDER_MAP = {
    "ollama": ollama.stream_chat_completion,
}

# How many fallback hops a single client request may take before we give up.
MAX_FALLBACK_DEPTH = 3


class RoutingError(Exception):
    pass


class RateLimitError(Exception):
    def __init__(self, message: str, retry_after_seconds: int = 60):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


@dataclass
class PreparedRequest:
    """Everything settled before a single byte goes to the provider.

    Streaming needs this split out: once an SSE body has started you can no
    longer return a 429 or a 502, so rate limiting, route resolution and input
    guardrails must all run — and be allowed to raise — before the response
    starts.
    """

    route_name: str
    route: Dict[str, Any]
    messages: List[Dict[str, str]]
    guardrails: List[Dict[str, Any]] = field(default_factory=list)
    intent: Optional[str] = None
    confidence: Optional[float] = None

    @property
    def policy(self) -> Dict[str, Any]:
        return self.route.get("policy", {}) or {}


async def prepare_request(
    route_name: str,
    messages: List[Dict[str, str]],
    client_id: str = "anonymous",
) -> PreparedRequest:
    """Resolve, rate-limit and sanitise. Raises before any provider I/O."""
    resolved_route, intent, confidence = await _resolve_route(route_name, messages)

    route = get_route(resolved_route)
    if not route:
        raise RoutingError(f"Route '{resolved_route}' not found or disabled")

    policy = route.get("policy", {}) or {}
    decision = check_rate_limit_detailed(
        resolved_route, policy.get("rate_limit_rpm", 60), client_id
    )
    if not decision.allowed:
        raise RateLimitError(
            f"Rate limit exceeded for route '{resolved_route}' "
            f"({decision.current}/{decision.limit} rpm)",
            retry_after_seconds=decision.retry_after_seconds,
        )

    guardrails = get_all_guardrails_for_route(route)
    try:
        clean_messages = apply_input_guardrails(messages, guardrails)
    except GuardrailViolation as exc:
        raise RoutingError(
            f"Input blocked by guardrail '{exc.guardrail_name}': {exc.detail}"
        ) from exc

    return PreparedRequest(
        route_name=resolved_route,
        route=route,
        messages=clean_messages,
        guardrails=guardrails,
        intent=intent,
        confidence=confidence,
    )


async def route_request(
    route_name: str,
    messages: List[Dict[str, str]],
    client_id: str = "anonymous",
    parent_trace_id: Optional[str] = None,
    _visited: Optional[FrozenSet[str]] = None,
) -> Dict[str, Any]:
    """Non-streaming entry point. route_name='auto' triggers classification."""
    visited = _visited or frozenset()

    prepared = await prepare_request(route_name, messages, client_id)
    resolved_route = prepared.route_name

    if resolved_route in visited:
        raise RoutingError(
            f"Fallback cycle detected: {' -> '.join([*visited, resolved_route])}"
        )
    if len(visited) >= MAX_FALLBACK_DEPTH:
        raise RoutingError(
            f"Fallback chain exceeded {MAX_FALLBACK_DEPTH} hops "
            f"({' -> '.join([*visited, resolved_route])})"
        )

    route = prepared.route
    messages = prepared.messages
    hedge_route = route.get("fallback")

    async def _call(rname: str, msgs: List) -> Dict[str, Any]:
        r = get_route(rname)
        if not r:
            raise RoutingError(f"Route '{rname}' not found")
        return await _provider_call(r, msgs)

    start = time.monotonic()
    try:
        if hedge_route and hedge_route != resolved_route:
            result = await hedged_call(
                primary_route_name=resolved_route,
                messages=messages,
                call_fn=_call,
                hedge_route_name=hedge_route,
            )
            # hedged_call records latency internally; skip double-recording.
        else:
            result = await _call(resolved_route, messages)
            record_latency(resolved_route, (time.monotonic() - start) * 1000)

    except Exception as exc:
        log.warning("gateway.provider_error route=%s error=%s", resolved_route, exc)
        if hedge_route and hedge_route != resolved_route:
            log.info("gateway.hard_fallback route=%s fallback=%s", resolved_route, hedge_route)
            return await route_request(
                hedge_route,
                messages,
                client_id,
                parent_trace_id,
                _visited=visited | {resolved_route},
            )
        raise RoutingError(f"Provider call failed: {exc}") from exc

    output_text = result["choices"][0]["message"]["content"]
    try:
        clean_output = apply_output_guardrails(output_text, prepared.guardrails)
        result["choices"][0]["message"]["content"] = clean_output
    except GuardrailViolation as exc:
        raise RoutingError(
            f"Output blocked by guardrail '{exc.guardrail_name}': {exc.detail}"
        ) from exc

    usage = resolve_usage(result.get("usage"), messages, clean_output)
    result["usage"] = usage
    record_usage(
        resolved_route,
        usage["prompt_tokens"],
        usage["completion_tokens"],
        prepared.policy.get("cost_per_1k_tokens", 0.0),
    )

    result["_route"] = resolved_route
    if prepared.intent:
        result["_intent"] = prepared.intent
        result["_confidence"] = prepared.confidence

    _emit_trace(
        prepared=prepared,
        output_text=clean_output,
        usage=usage,
        duration_ms=(time.monotonic() - start) * 1000,
        parent_trace_id=parent_trace_id,
        hedged=result.get("_hedged", False),
        hedge_winner=result.get("_hedge_winner"),
    )

    return result


async def stream_request(
    prepared: PreparedRequest,
    parent_trace_id: Optional[str] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """Yield OpenAI-compatible chunks for an already-prepared request.

    Not hedged and not failed over: both need a second response body, and the
    first byte of this one has already reached the client. A provider error
    mid-stream surfaces as an error chunk, not a retry on another route.
    """
    route = prepared.route
    provider = route["provider"]
    fn = _STREAM_PROVIDER_MAP.get(provider)
    if not fn:
        raise RoutingError(f"Streaming not supported for provider: {provider}")

    policy = prepared.policy
    guard = StreamGuard(compile_stream_rules(prepared.guardrails))
    reported_usage: Optional[Dict[str, int]] = None
    start = time.monotonic()

    try:
        async for chunk in fn(
            base_url=route.get("base_url", ""),
            model=route["model"],
            messages=prepared.messages,
            max_tokens=policy.get("max_tokens", 4096),
            temperature=policy.get("temperature", 0.7),
        ):
            if "_usage" in chunk:
                reported_usage = chunk.pop("_usage")

            choice = chunk["choices"][0]
            delta = choice.get("delta", {}) or {}

            if "content" in delta:
                safe = guard.push(delta.get("content") or "")
                # Suppress a chunk whose text is entirely held back, unless it
                # also carries the role or a finish_reason the client needs.
                if not safe and not delta.get("role") and choice.get("finish_reason") is None:
                    continue
                delta["content"] = safe

            if choice.get("finish_reason") is not None:
                tail = guard.flush()
                if tail:
                    yield _text_chunk(chunk, tail)

            yield chunk

    except GuardrailViolation as exc:
        log.warning("gateway.stream_blocked guardrail=%s", exc.guardrail_name)
        yield _error_chunk(
            prepared.route_name,
            f"Output blocked by guardrail '{exc.guardrail_name}': {exc.detail}",
        )
        return
    except Exception as exc:
        log.warning("gateway.stream_error route=%s error=%s", prepared.route_name, exc)
        yield _error_chunk(prepared.route_name, f"Provider stream failed: {exc}")
        return

    duration_ms = (time.monotonic() - start) * 1000
    record_latency(prepared.route_name, duration_ms)

    usage = resolve_usage(reported_usage, prepared.messages, guard.released)
    record_usage(
        prepared.route_name,
        usage["prompt_tokens"],
        usage["completion_tokens"],
        policy.get("cost_per_1k_tokens", 0.0),
    )

    _emit_trace(
        prepared=prepared,
        output_text=guard.released,
        usage=usage,
        duration_ms=duration_ms,
        parent_trace_id=parent_trace_id,
    )

    yield {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion.chunk",
        "model": route.get("model", "unknown"),
        "choices": [],
        "usage": usage,
        "_route": prepared.route_name,
    }


# ── Internals ─────────────────────────────────────────────────────────────────

def _text_chunk(template: Dict[str, Any], text: str) -> Dict[str, Any]:
    return {
        "id": template.get("id"),
        "object": "chat.completion.chunk",
        "created": template.get("created"),
        "model": template.get("model"),
        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
    }


def _error_chunk(route_name: str, message: str) -> Dict[str, Any]:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion.chunk",
        "model": route_name,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "content_filter"}],
        "error": {"message": message, "type": "gateway_error"},
    }


def _emit_trace(
    prepared: PreparedRequest,
    output_text: str,
    usage: Dict[str, int],
    duration_ms: float,
    parent_trace_id: Optional[str],
    hedged: bool = False,
    hedge_winner: Optional[str] = None,
) -> None:
    obs_configs = get_observability_configs()
    if not obs_configs:
        return

    cost_per_1k = prepared.policy.get("cost_per_1k_tokens", 0.0)
    trace = LLMTrace(
        trace_id=str(uuid.uuid4()),
        parent_trace_id=parent_trace_id,
        route_name=prepared.route_name,
        provider=prepared.route.get("provider", "unknown"),
        model=prepared.route.get("model", "unknown"),
        input_messages=prepared.messages,
        output_content=output_text,
        latency_ms=duration_ms,
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        total_tokens=usage["total_tokens"],
        cost_usd=(usage["total_tokens"] / 1000) * cost_per_1k,
        intent=prepared.intent,
        confidence=prepared.confidence,
        hedged=hedged,
        hedge_winner=hedge_winner,
    )
    asyncio.create_task(dispatch_trace(trace, obs_configs))


async def _resolve_route(
    route_name: str, messages: List[Dict[str, str]]
) -> tuple[str, Optional[str], Optional[float]]:
    """Return (resolved_route_name, intent, confidence)."""
    if route_name != "auto":
        return route_name, None, None

    rules = get_semantic_rules()
    if not rules:
        raise RoutingError("route='auto' requested but no semantic routing rules are configured")

    intent, resolved, confidence = await classify_intent(
        messages, rules, settings.classifier_base_url, settings.classifier_model
    )
    log.info(
        "gateway.semantic_route_selected intent=%s route=%s confidence=%.3f",
        intent, resolved, confidence,
    )
    return resolved, intent, confidence


async def _provider_call(route: Dict, messages: List) -> Dict[str, Any]:
    provider = route["provider"]
    fn = _PROVIDER_MAP.get(provider)
    if not fn:
        raise RoutingError(f"Unsupported provider: {provider}")
    policy = route.get("policy", {}) or {}
    return await fn(
        base_url=route.get("base_url", ""),
        model=route["model"],
        messages=messages,
        max_tokens=policy.get("max_tokens", 4096),
        temperature=policy.get("temperature", 0.7),
    )
