"""DeepEval / Confident AI adapter.

DeepEval ships LLM test cases to Confident AI cloud for online evaluation.
Each trace is logged as a test case that can be scored against metrics
(answer relevancy, faithfulness, hallucination, etc.) asynchronously.

Docs: https://docs.confident-ai.com
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

import httpx

from app.middleware.observability.base import LLMTrace, ObservabilityAdapter

log = logging.getLogger(__name__)


class DeepEvalAdapter(ObservabilityAdapter):
    provider_name = "deepeval"

    async def send_trace(self, trace: LLMTrace, config: Dict[str, Any]) -> None:
        creds = config["credentials"]
        api_key = creds["api_key"]
        host = (config.get("endpoint_url") or "https://api.confident-ai.com").rstrip("/")
        project_name = config.get("project_name", "llm-gateway")

        # DeepEval online test case format
        test_case = {
            "traceId": trace.trace_id,
            "projectName": project_name,
            "datasetAlias": f"{trace.route_name}-production",
            "input": _messages_to_str(trace.input_messages),
            "actualOutput": trace.output_content,
            "context": [],
            "retrievalContext": [],
            "metadata": {
                "route": trace.route_name,
                "provider": trace.provider,
                "model": trace.model,
                "intent": trace.intent,
                "confidence": trace.confidence,
                "latency_ms": trace.latency_ms,
                "prompt_tokens": trace.prompt_tokens,
                "completion_tokens": trace.completion_tokens,
                "cost_usd": trace.cost_usd,
                "hedged": trace.hedged,
                **trace.metadata,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            async with httpx.AsyncClient(timeout=5, headers={"Authorization": f"Bearer {api_key}"}) as client:
                resp = await client.post(f"{host}/api/v1/test-cases/online", json=test_case)
                resp.raise_for_status()
                log.debug("deepeval.test_case_sent", trace_id=trace.trace_id)
        except Exception:
            log.exception("deepeval.send_failed", trace_id=trace.trace_id)


def _messages_to_str(messages) -> str:
    return "\n".join(f"{m['role']}: {m['content']}" for m in messages)
