"""Core routing logic.

Request lifecycle:
  1. Resolve route name  →  explicit name OR semantic classification (route='auto')
  2. Rate limit check    →  Redis sliding window
  3. Guardrail input     →  PII / toxicity / regex / keyword filters
  4. Provider call       →  direct OR hedged (fire primary + secondary in parallel)
  5. Record latency      →  rolling P95 in Redis
  6. Guardrail output    →  filter LLM response
  7. Cost tracking       →  token usage → estimated USD in Redis
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from app.config import settings
from app.middleware.cost_tracker import record_usage
from app.middleware.guardrails import GuardrailViolation, apply_input_guardrails, apply_output_guardrails
from app.middleware.latency_tracker import record_latency
from app.middleware.observability.base import LLMTrace
from app.middleware.observability.tracer import dispatch_trace
from app.middleware.rate_limit import check_rate_limit
from app.providers import ollama
from app.router.classifier import classify_intent
from app.router.config_store import get_all_guardrails_for_route, get_observability_configs, get_route, get_semantic_rules
from app.router.hedged_request import hedged_call

log = logging.getLogger(__name__)

_PROVIDER_MAP = {
    "ollama": ollama.chat_completion,
}


class RoutingError(Exception):
    pass


class RateLimitError(Exception):
    pass


async def route_request(
    route_name: str,
    messages: List[Dict[str, str]],
    client_id: str = "anonymous",
    stream: bool = False,
    parent_trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Main entry point. route_name='auto' triggers semantic classification."""

    resolved_route, intent, confidence = await _resolve_route(route_name, messages)

    route = get_route(resolved_route)
    if not route:
        raise RoutingError(f"Route '{resolved_route}' not found or disabled")

    policy = route.get("policy", {})
    if not check_rate_limit(resolved_route, policy.get("rate_limit_rpm", 60), client_id):
        raise RateLimitError(f"Rate limit exceeded for route '{resolved_route}'")

    guardrails = get_all_guardrails_for_route(route)
    input_text = _messages_to_text(messages)

    try:
        cleaned_input = apply_input_guardrails(input_text, guardrails)
        if cleaned_input != input_text:
            messages = _update_last_user_message(messages, cleaned_input)
    except GuardrailViolation as exc:
        raise RoutingError(f"Input blocked by guardrail '{exc.guardrail_name}': {exc.detail}") from exc

    # Determine hedge route: use the route's configured fallback as the secondary
    hedge_route = route.get("fallback")

    async def _call(rname: str, msgs: List) -> Dict[str, Any]:
        r = get_route(rname)
        if not r:
            raise RoutingError(f"Route '{rname}' not found")
        return await _provider_call(r, msgs, stream)

    start = time.monotonic()
    try:
        if hedge_route and not stream:
            result = await hedged_call(
                primary_route_name=resolved_route,
                messages=messages,
                call_fn=_call,
                hedge_route_name=hedge_route,
            )
            # hedged_call records latency internally; skip double-recording
        else:
            result = await _call(resolved_route, messages)
            duration_ms = (time.monotonic() - start) * 1000
            record_latency(resolved_route, duration_ms)

    except Exception as exc:
        log.warning("gateway.provider_error", route=resolved_route, error=str(exc))
        if hedge_route and hedge_route != resolved_route:
            log.info("gateway.hard_fallback", route=resolved_route, fallback=hedge_route)
            return await route_request(hedge_route, messages, client_id, stream)
        raise RoutingError(f"Provider call failed: {exc}") from exc

    output_text = result["choices"][0]["message"]["content"]
    try:
        clean_output = apply_output_guardrails(output_text, guardrails)
        result["choices"][0]["message"]["content"] = clean_output
    except GuardrailViolation as exc:
        raise RoutingError(f"Output blocked by guardrail '{exc.guardrail_name}': {exc.detail}") from exc

    usage = result.get("usage", {})
    record_usage(
        resolved_route,
        usage.get("prompt_tokens", 0),
        usage.get("completion_tokens", 0),
        policy.get("cost_per_1k_tokens", 0.0),
    )

    result["_route"] = resolved_route
    if intent:
        result["_intent"] = intent
        result["_confidence"] = confidence

    # Fire-and-forget: ship trace to all matching observability backends
    obs_configs = get_observability_configs()
    if obs_configs:
        duration_ms = (time.monotonic() - start) * 1000
        trace = LLMTrace(
            trace_id=str(uuid.uuid4()),
            parent_trace_id=parent_trace_id,
            route_name=resolved_route,
            provider=route.get("provider", "unknown"),
            model=route.get("model", "unknown"),
            input_messages=messages,
            output_content=result["choices"][0]["message"]["content"],
            latency_ms=duration_ms,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            cost_usd=(usage.get("total_tokens", 0) / 1000) * route.get("policy", {}).get("cost_per_1k_tokens", 0.0),
            intent=intent,
            confidence=confidence,
            hedged=result.get("_hedged", False),
            hedge_winner=result.get("_hedge_winner"),
        )
        asyncio.create_task(dispatch_trace(trace, obs_configs))

    return result


async def _resolve_route(
    route_name: str, messages: List[Dict[str, str]]
) -> tuple[str, Optional[str], Optional[float]]:
    """Return (resolved_route_name, intent, confidence).

    If route_name is 'auto', runs the semantic classifier.
    Otherwise returns the explicit name with no intent metadata.
    """
    if route_name != "auto":
        return route_name, None, None

    rules = get_semantic_rules()
    if not rules:
        raise RoutingError("route='auto' requested but no semantic routing rules are configured")

    ollama_base = settings.classifier_base_url
    ollama_model = settings.classifier_model

    intent, resolved, confidence = await classify_intent(messages, rules, ollama_base, ollama_model)
    log.info(
        "gateway.semantic_route_selected",
        intent=intent,
        route=resolved,
        confidence=round(confidence, 3),
    )
    return resolved, intent, confidence


async def _provider_call(route: Dict, messages: List, stream: bool) -> Dict[str, Any]:
    provider = route["provider"]
    fn = _PROVIDER_MAP.get(provider)
    if not fn:
        raise RoutingError(f"Unsupported provider: {provider}")
    policy = route.get("policy", {})
    return await fn(
        base_url=route.get("base_url", ""),
        model=route["model"],
        messages=messages,
        max_tokens=policy.get("max_tokens", 4096),
        temperature=policy.get("temperature", 0.7),
        stream=stream,
    )


def _messages_to_text(messages: List[Dict[str, str]]) -> str:
    return " ".join(m.get("content", "") for m in messages)


def _update_last_user_message(messages: List[Dict[str, str]], new_content: str) -> List[Dict[str, str]]:
    msgs = list(messages)
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].get("role") == "user":
            msgs[i] = {**msgs[i], "content": new_content}
            break
    return msgs
