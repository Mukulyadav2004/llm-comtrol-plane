"""Braintrust adapter — logs experiments/spans via their REST API.

Each LLM call becomes a span inside an experiment or the production log.
Docs: https://www.braintrust.dev/docs/reference/rest
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict

import httpx

from app.middleware.observability.base import LLMTrace, ObservabilityAdapter

log = logging.getLogger(__name__)


class BraintrustAdapter(ObservabilityAdapter):
    provider_name = "braintrust"

    async def send_trace(self, trace: LLMTrace, config: Dict[str, Any]) -> None:
        creds = config["credentials"]
        api_key = creds["api_key"]
        host = (config.get("endpoint_url") or "https://api.braintrust.dev").rstrip("/")
        project_id = config.get("remote_project_id", "")

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(milliseconds=trace.latency_ms)

        span = {
            "id": trace.trace_id,
            "project_id": project_id,
            "log_id": "production",
            "span_attributes": {"name": "llm-call", "type": "llm"},
            "input": trace.input_messages,
            "output": trace.output_content,
            "metadata": {
                "route": trace.route_name,
                "provider": trace.provider,
                "model": trace.model,
                "intent": trace.intent,
                "confidence": trace.confidence,
                "hedged": trace.hedged,
                **trace.metadata,
            },
            "metrics": {
                "start": start_time.timestamp(),
                "end": end_time.timestamp(),
                "prompt_tokens": trace.prompt_tokens,
                "completion_tokens": trace.completion_tokens,
                "tokens": trace.total_tokens,
                "cost": trace.cost_usd,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=5, headers={"Authorization": f"Bearer {api_key}"}) as client:
                resp = await client.post(f"{host}/v1/project_logs/{project_id}/insert", json={"events": [span]})
                resp.raise_for_status()
                log.debug("braintrust.span_sent", trace_id=trace.trace_id)
        except Exception:
            log.exception("braintrust.send_failed", trace_id=trace.trace_id)
