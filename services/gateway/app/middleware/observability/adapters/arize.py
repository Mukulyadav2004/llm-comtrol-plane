"""Arize AI adapter — ships spans via their REST API.

Arize ingests prediction records: input (prompt) + output (response) + metadata.
Docs: https://docs.arize.com/arize/api-reference
"""
from __future__ import annotations

import logging
from typing import Any, Dict

import httpx

from app.middleware.observability.base import LLMTrace, ObservabilityAdapter

log = logging.getLogger(__name__)


class ArizeAdapter(ObservabilityAdapter):
    provider_name = "arize"

    async def send_trace(self, trace: LLMTrace, config: Dict[str, Any]) -> None:
        creds = config["credentials"]
        api_key = creds["api_key"]
        space_key = creds["space_key"]
        project_name = config.get("project_name", "llm-gateway")

        # Arize log_llm_prediction shape
        body = {
            "spaceKey": space_key,
            "modelId": trace.route_name,
            "modelVersion": trace.model,
            "predictionId": trace.trace_id,
            "predictionLabel": {
                "llmResponse": {
                    "response": trace.output_content,
                }
            },
            "features": {
                "prompt": _messages_to_str(trace.input_messages),
                "route": trace.route_name,
                "provider": trace.provider,
                "model": trace.model,
                "intent": trace.intent or "",
                "confidence": trace.confidence or 0.0,
                "hedged": str(trace.hedged),
            },
            "tags": {
                "project": project_name,
            },
            "latencyMs": trace.latency_ms,
            "promptTokenCount": trace.prompt_tokens,
            "completionTokenCount": trace.completion_tokens,
        }

        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(
                    "https://api.arize.com/v1/log",
                    json=body,
                    headers={"Authorization": f"Bearer {api_key}", "Space-Key": space_key},
                )
                resp.raise_for_status()
                log.debug("arize.trace_sent", trace_id=trace.trace_id)
        except Exception:
            log.exception("arize.send_failed", trace_id=trace.trace_id)


def _messages_to_str(messages) -> str:
    return "\n".join(f"{m['role']}: {m['content']}" for m in messages)
