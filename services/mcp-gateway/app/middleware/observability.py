"""Tool call observability — sends ToolCallTrace to all provisioned backends.

Uses the same adapter pattern as the LLM Gateway but with a ToolCallTrace
shape suited for MCP: tool name, arguments, result, latency, caller identity.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger(__name__)


@dataclass
class ToolCallTrace:
    trace_id: str
    tool_name: str
    server_name: str
    caller_id: str            # agent session id or direct client id

    arguments: Dict[str, Any]
    result: Any
    error: Optional[str]

    latency_ms: float
    success: bool

    # Parent trace id (agent run / client trace) for end-to-end nesting.
    parent_trace_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


async def dispatch_tool_trace(trace: ToolCallTrace, obs_configs: List[Dict[str, Any]]) -> None:
    tasks = [_send(trace, cfg) for cfg in obs_configs if cfg.get("enabled") and cfg.get("provisioned")]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _send(trace: ToolCallTrace, config: Dict[str, Any]) -> None:
    provider = config.get("provider")
    try:
        if provider == "langfuse":
            await _send_langfuse(trace, config)
        elif provider == "langsmith":
            await _send_langsmith(trace, config)
        elif provider == "braintrust":
            await _send_braintrust(trace, config)
        # arize + deepeval: same pattern, abbreviated for clarity
        else:
            log.debug("mcp_obs.provider_not_implemented", provider=provider)
    except Exception:
        log.exception("mcp_obs.send_failed", provider=provider, trace_id=trace.trace_id)


async def _send_langfuse(trace: ToolCallTrace, config: Dict) -> None:
    creds = config["credentials"]
    host = (config.get("endpoint_url") or creds.get("host") or "https://cloud.langfuse.com").rstrip("/")
    body = {
        "batch": [{
            "id": str(uuid.uuid4()),
            "type": "span-create",
            "body": {
                "traceId": trace.parent_trace_id or trace.trace_id,
                "name": f"tool:{trace.tool_name}",
                "input": trace.arguments,
                "output": trace.result,
                "metadata": {"server": trace.server_name, "caller": trace.caller_id, "success": trace.success, "error": trace.error},
                "latency": trace.latency_ms / 1000,
            },
        }]
    }
    async with httpx.AsyncClient(timeout=5, auth=(creds["public_key"], creds["secret_key"])) as c:
        (await c.post(f"{host}/api/public/ingestion", json=body)).raise_for_status()


async def _send_langsmith(trace: ToolCallTrace, config: Dict) -> None:
    creds = config["credentials"]
    host = (config.get("endpoint_url") or "https://api.smith.langchain.com").rstrip("/")
    end = datetime.now(timezone.utc)
    start = end - timedelta(milliseconds=trace.latency_ms)
    run = {
        "id": trace.trace_id,
        "name": f"tool:{trace.tool_name}",
        "run_type": "tool",
        "session_name": config.get("project_name", "mcp-gateway"),
        "inputs": trace.arguments,
        "outputs": {"result": trace.result},
        "error": trace.error,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
    }
    async with httpx.AsyncClient(timeout=5, headers={"x-api-key": creds["api_key"]}) as c:
        (await c.post(f"{host}/api/v1/runs", json=run)).raise_for_status()


async def _send_braintrust(trace: ToolCallTrace, config: Dict) -> None:
    creds = config["credentials"]
    host = (config.get("endpoint_url") or "https://api.braintrust.dev").rstrip("/")
    project_id = config.get("remote_project_id", "")
    end = datetime.now(timezone.utc)
    start = end - timedelta(milliseconds=trace.latency_ms)
    span = {
        "id": trace.trace_id,
        "project_id": project_id,
        "log_id": "production",
        "span_attributes": {"name": f"tool:{trace.tool_name}", "type": "tool"},
        "input": trace.arguments,
        "output": trace.result,
        "error": trace.error,
        "metrics": {"start": start.timestamp(), "end": end.timestamp()},
        "metadata": {"server": trace.server_name, "caller": trace.caller_id},
    }
    async with httpx.AsyncClient(timeout=5, headers={"Authorization": f"Bearer {creds['api_key']}"}) as c:
        (await c.post(f"{host}/v1/project_logs/{project_id}/insert", json={"events": [span]})).raise_for_status()
