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
from app.providers import (
    ProviderAuthError,
    ProviderError,
    ProviderRequest,
    get_provider,
)
from app.providers.credentials import resolve_api_key
from app.router.classifier import classify_intent
from app.router.config_store import (
    get_all_guardrails_for_route,
    get_observability_configs,
    get_route,
    get_semantic_rules,
)
from app.router.hedged_request import hedged_call

log = logging.getLogger(__name__)

# How many fallback hops a single client request may take before we give up.
MAX_FALLBACK_DEPTH = 3


class RoutingError(Exception):
    """The upstream could not serve this request. Maps to 502."""


class GatewayConfigError(Exception):
    """This gateway cannot serve the route because *its own* config is wrong.

    An unset api_key_env or an unregistered provider name is not an upstream
    fault — nothing was ever sent upstream. Reporting it as 502 Bad Gateway
    points the operator at the wrong system. Maps to 500.
    """


class RateLimiterUnavailableError(Exception):
    """The shared limiter is unreachable and the policy is to reject.

    Distinct from RateLimitError on purpose: telling a caller they exceeded a
    quota they did not exceed is a lie, and sends them to the wrong fix. Maps
    to 503.
    """


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

    # Settle "can this gateway serve the route at all" before spending a rate
    # limit slot or touching an upstream. It also has to happen here rather than
    # at call time so the streaming path reports it as a status code: once the
    # SSE body is open, a config error can only arrive as a 200 with an error
    # chunk in it.
    assert_route_is_servable(route)

    policy = route.get("policy", {}) or {}
    decision = check_rate_limit_detailed(
        resolved_route, policy.get("rate_limit_rpm", 60), client_id
    )
    if not decision.allowed:
        if decision.degraded:
            raise RateLimiterUnavailableError(
                "Rate limiter backend is unavailable and the gateway is "
                "configured to reject requests while it is down."
            )
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

    hedging = bool(hedge_route and hedge_route != resolved_route)
    start = time.monotonic()
    try:
        if hedging:
            # hedged_call records latency internally for whichever route won.
            result = await hedged_call(
                primary_route_name=resolved_route,
                messages=messages,
                call_fn=_call,
                hedge_route_name=hedge_route,
            )
        else:
            result = await _call(resolved_route, messages)

    except Exception as exc:
        log.warning("gateway.provider_error route=%s error=%s", resolved_route, exc)
        # The gateway's own misconfiguration is not something a second upstream
        # can fix, and must not be reported as an upstream failure.
        if isinstance(exc, GatewayConfigError):
            raise
        # A 400 or a bad API key will fail identically on the fallback route, so
        # trying it just burns a second upstream and doubles the latency of an
        # error the caller has to fix anyway. Only retryable failures fall back.
        if isinstance(exc, ProviderError) and not exc.retryable:
            raise RoutingError(f"Provider call failed: {exc}") from exc
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

    # Telemetry lives outside the try. Inside it, a Redis blip during
    # record_latency was caught by the `except` above, reported as a provider
    # error, and — not being a non-retryable ProviderError — fell through to the
    # fallback route, discarding a response that had already succeeded and
    # billing a second upstream call for it.
    if not hedging:
        record_latency(resolved_route, (time.monotonic() - start) * 1000)

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
    provider, provider_req = build_provider_request(route, prepared.messages)
    if not provider.supports_streaming:
        raise RoutingError(f"Streaming not supported for provider: {provider.name}")

    policy = prepared.policy
    guard = StreamGuard(compile_stream_rules(prepared.guardrails))
    reported_usage: Optional[Dict[str, int]] = None
    start = time.monotonic()

    try:
        async for chunk in provider.stream_chat_completion(provider_req):
            if "_usage" in chunk:
                reported_usage = chunk.pop("_usage")

            # Providers disagree about where usage goes: Ollama attaches it to
            # the finish frame, OpenAI-compatible servers send a trailing
            # choices-free chunk, Anthropic splits it across two events. Having
            # harvested it above, a chunk with no choices carries nothing else.
            if not chunk.get("choices"):
                continue

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


def build_provider_request(route: Dict, messages: List) -> tuple:
    """Resolve a route to (provider, ProviderRequest).

    The credential is looked up here rather than being carried in the route, so
    a secret never reaches the Broker, the Control Plane, or the Redis cache the
    config is served from. See app/providers/credentials.py.
    """
    provider = _resolve_provider(route)
    policy = route.get("policy", {}) or {}
    req = ProviderRequest(
        model=route["model"],
        messages=messages,
        max_tokens=policy.get("max_tokens", 4096),
        temperature=policy.get("temperature", 0.7),
        stop=policy.get("stop"),
        base_url=route.get("base_url") or None,
        api_key=_resolve_credential(provider, route),
        timeout=float(policy.get("timeout_seconds", settings.request_timeout)),
        extra=route.get("provider_options", {}) or {},
    )
    return provider, req


def _resolve_provider(route: Dict) -> Any:
    try:
        return get_provider(route["provider"])
    except KeyError as exc:
        raise GatewayConfigError(str(exc)) from exc


def _resolve_credential(provider: Any, route: Dict) -> Optional[str]:
    try:
        return resolve_api_key(provider, route)
    except ProviderAuthError as exc:
        raise GatewayConfigError(str(exc)) from exc


def assert_route_is_servable(route: Dict) -> None:
    """Raise GatewayConfigError if this build cannot serve the route as written."""
    _resolve_credential(_resolve_provider(route), route)


async def _provider_call(route: Dict, messages: List) -> Dict[str, Any]:
    provider, req = build_provider_request(route, messages)
    return await provider.chat_completion(req)
