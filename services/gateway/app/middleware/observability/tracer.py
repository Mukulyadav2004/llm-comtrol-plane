"""Observability tracer — dispatches LLMTrace to all matching provider adapters.

Called from llm_router as a fire-and-forget asyncio.create_task() so it never
adds latency to the critical path.

Config comes from the control plane (gateway_config.json.j2 → observability_configs).
Each config specifies:
  - provider: which adapter to use
  - route_names: which LLM routes it applies to (empty = all)
  - credentials + endpoint_url: passed straight to the adapter
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from app.middleware.observability.adapters.langfuse import LangfuseAdapter
from app.middleware.observability.adapters.arize import ArizeAdapter
from app.middleware.observability.adapters.langsmith import LangSmithAdapter
from app.middleware.observability.adapters.braintrust import BraintrustAdapter
from app.middleware.observability.adapters.deepeval import DeepEvalAdapter
from app.middleware.observability.base import LLMTrace, ObservabilityAdapter

log = logging.getLogger(__name__)

_ADAPTERS: Dict[str, ObservabilityAdapter] = {
    "langfuse": LangfuseAdapter(),
    "arize": ArizeAdapter(),
    "langsmith": LangSmithAdapter(),
    "braintrust": BraintrustAdapter(),
    "deepeval": DeepEvalAdapter(),
}


async def dispatch_trace(trace: LLMTrace, obs_configs: List[Dict[str, Any]]) -> None:
    """Fan-out: send trace to every matching observability backend concurrently."""
    tasks = []
    for config in obs_configs:
        if not config.get("enabled", True) or not config.get("provisioned", False):
            continue

        route_names = config.get("route_names", [])
        if route_names and trace.route_name not in route_names:
            continue

        provider = config.get("provider")
        adapter = _ADAPTERS.get(provider)
        if not adapter:
            log.warning("tracer.unknown_provider", provider=provider)
            continue

        tasks.append(_safe_send(adapter, trace, config))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _safe_send(adapter: ObservabilityAdapter, trace: LLMTrace, config: Dict) -> None:
    try:
        await adapter.send_trace(trace, config)
    except Exception:
        log.exception("tracer.dispatch_error", provider=adapter.provider_name, trace_id=trace.trace_id)
