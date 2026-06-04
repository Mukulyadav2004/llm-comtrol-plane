"""Langfuse adapter — ships traces via their REST ingestion API.

Langfuse trace model:
  Trace → Generation (one per LLM call)

Docs: https://langfuse.com/docs/api
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict

import httpx

from app.middleware.observability.base import LLMTrace, ObservabilityAdapter

log = logging.getLogger(__name__)


class LangfuseAdapter(ObservabilityAdapter):
    provider_name = "langfuse"

    async def send_trace(self, trace: LLMTrace, config: Dict[str, Any]) -> None:
        creds = config["credentials"]
        host = (config.get("endpoint_url") or creds.get("host") or "https://cloud.langfuse.com").rstrip("/")
        public_key = creds["public_key"]
        secret_key = creds["secret_key"]
        project_id = config.get("remote_project_id", "")

        # If this call belongs to a parent trace (e.g. an agent run), nest the
        # generation under it rather than creating a new root trace. Otherwise
        # the LLM call is its own root.
        root_trace_id = trace.parent_trace_id or trace.trace_id

        batch = []
        if not trace.parent_trace_id:
            batch.append({
                "id": trace.trace_id,
                "type": "trace-create",
                "body": {
                    "id": trace.trace_id,
                    "name": f"{trace.route_name}/{trace.intent or 'direct'}",
                    "metadata": {
                        "route": trace.route_name,
                        "provider": trace.provider,
                        "model": trace.model,
                        "intent": trace.intent,
                        "confidence": trace.confidence,
                        "hedged": trace.hedged,
                        **trace.metadata,
                    },
                    "tags": [trace.provider, trace.route_name],
                },
            })

        body = {
            "batch": batch + [
                {
                    "id": str(uuid.uuid4()),
                    "type": "generation-create",
                    "body": {
                        "traceId": root_trace_id,
                        "name": "llm-call",
                        "model": trace.model,
                        "modelParameters": {"provider": trace.provider},
                        "input": trace.input_messages,
                        "output": trace.output_content,
                        "usage": {
                            "promptTokens": trace.prompt_tokens,
                            "completionTokens": trace.completion_tokens,
                            "totalTokens": trace.total_tokens,
                        },
                        "latency": trace.latency_ms / 1000,
                        "calculatedTotalCost": trace.cost_usd,
                    },
                },
            ]
        }

        try:
            async with httpx.AsyncClient(timeout=5, auth=(public_key, secret_key)) as client:
                resp = await client.post(f"{host}/api/public/ingestion", json=body)
                resp.raise_for_status()
                log.debug("langfuse.trace_sent", trace_id=trace.trace_id)
        except Exception:
            log.exception("langfuse.send_failed", trace_id=trace.trace_id)
