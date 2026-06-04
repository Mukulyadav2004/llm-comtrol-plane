"""LangSmith adapter — ships runs via their REST API.

LangSmith models each inference as a "run" of type "llm".
Docs: https://api.smith.langchain.com/redoc
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict

import httpx

from app.middleware.observability.base import LLMTrace, ObservabilityAdapter

log = logging.getLogger(__name__)


class LangSmithAdapter(ObservabilityAdapter):
    provider_name = "langsmith"

    async def send_trace(self, trace: LLMTrace, config: Dict[str, Any]) -> None:
        creds = config["credentials"]
        api_key = creds["api_key"]
        host = (config.get("endpoint_url") or "https://api.smith.langchain.com").rstrip("/")
        project_name = config.get("project_name", "llm-gateway")

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(milliseconds=trace.latency_ms)

        run = {
            "id": trace.trace_id,
            "name": f"{trace.route_name}-call",
            "run_type": "llm",
            "session_name": project_name,
            "inputs": {"messages": trace.input_messages},
            "outputs": {"content": trace.output_content},
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "extra": {
                "metadata": {
                    "route": trace.route_name,
                    "provider": trace.provider,
                    "model": trace.model,
                    "intent": trace.intent,
                    "confidence": trace.confidence,
                    "hedged": trace.hedged,
                    "cost_usd": trace.cost_usd,
                    **trace.metadata,
                },
                "invocation_params": {
                    "model": trace.model,
                    "provider": trace.provider,
                },
            },
            "token_usage": {
                "prompt_tokens": trace.prompt_tokens,
                "completion_tokens": trace.completion_tokens,
                "total_tokens": trace.total_tokens,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=5, headers={"x-api-key": api_key}) as client:
                resp = await client.post(f"{host}/api/v1/runs", json=run)
                resp.raise_for_status()
                log.debug("langsmith.run_sent", trace_id=trace.trace_id)
        except Exception:
            log.exception("langsmith.send_failed", trace_id=trace.trace_id)
