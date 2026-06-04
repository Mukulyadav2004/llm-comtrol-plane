"""Agent run observability — sends AgentRunTrace to all provisioned backends.

An AgentRunTrace captures the full execution of one agent session:
  goal (task) → steps (think + act) → final answer + token budget + latency.

This gives you end-to-end visibility: one trace per agent run, with
nested spans for each LLM call (think) and tool call (act).
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
class AgentRunTrace:
    session_id: str
    task: str
    route: str
    final_answer: str
    status: str

    steps: List[Dict[str, Any]]
    total_tokens: int
    total_latency_ms: float

    think_steps: int = 0
    act_steps: int = 0
    tool_names_used: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


async def dispatch_agent_trace(trace: AgentRunTrace, obs_configs: List[Dict[str, Any]]) -> None:
    tasks = [_send(trace, cfg) for cfg in obs_configs if cfg.get("enabled") and cfg.get("provisioned")]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _send(trace: AgentRunTrace, config: Dict) -> None:
    provider = config.get("provider")
    try:
        if provider == "langfuse":
            await _send_langfuse(trace, config)
        elif provider == "langsmith":
            await _send_langsmith(trace, config)
        elif provider == "braintrust":
            await _send_braintrust(trace, config)
        else:
            log.debug("agent_obs.provider_not_implemented", provider=provider)
    except Exception:
        log.exception("agent_obs.send_failed", provider=provider, session_id=trace.session_id)


async def _send_langfuse(trace: AgentRunTrace, config: Dict) -> None:
    creds = config["credentials"]
    host = (config.get("endpoint_url") or creds.get("host") or "https://cloud.langfuse.com").rstrip("/")

    batch = [
        {
            "id": trace.session_id,
            "type": "trace-create",
            "body": {
                "id": trace.session_id,
                "name": f"agent-run/{trace.route}",
                "input": trace.task,
                "output": trace.final_answer,
                "metadata": {
                    "route": trace.route,
                    "status": trace.status,
                    "total_tokens": trace.total_tokens,
                    "steps": len(trace.steps),
                    "tools_used": trace.tool_names_used,
                    **trace.metadata,
                },
                "tags": ["agent", trace.route, trace.status],
            },
        }
    ]

    # Add individual step spans
    for i, step in enumerate(trace.steps):
        span_id = str(uuid.uuid4())
        if step["type"] == "think":
            batch.append({
                "id": span_id,
                "type": "generation-create",
                "body": {
                    "traceId": trace.session_id,
                    "name": f"think-step-{step['step']}",
                    "input": [],
                    "output": step.get("content", ""),
                    "usage": {"totalTokens": step.get("tokens_used", 0)},
                    "latency": step.get("latency_ms", 0) / 1000,
                },
            })
        elif step["type"] == "act":
            batch.append({
                "id": span_id,
                "type": "span-create",
                "body": {
                    "traceId": trace.session_id,
                    "name": f"tool:{step.get('tool', 'unknown')}",
                    "input": step.get("arguments", {}),
                    "output": step.get("observation"),
                    "latency": step.get("latency_ms", 0) / 1000,
                    "metadata": {"error": step.get("error")},
                },
            })

    async with httpx.AsyncClient(timeout=10, auth=(creds["public_key"], creds["secret_key"])) as c:
        (await c.post(f"{host}/api/public/ingestion", json={"batch": batch})).raise_for_status()


async def _send_langsmith(trace: AgentRunTrace, config: Dict) -> None:
    creds = config["credentials"]
    host = (config.get("endpoint_url") or "https://api.smith.langchain.com").rstrip("/")
    end = datetime.now(timezone.utc)
    start = end - timedelta(milliseconds=trace.total_latency_ms)

    run = {
        "id": trace.session_id,
        "name": f"agent-run/{trace.route}",
        "run_type": "chain",
        "session_name": config.get("project_name", "agent-gateway"),
        "inputs": {"task": trace.task},
        "outputs": {"answer": trace.final_answer},
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "extra": {
            "metadata": {
                "route": trace.route,
                "status": trace.status,
                "total_tokens": trace.total_tokens,
                "tools_used": trace.tool_names_used,
                "steps": len(trace.steps),
            }
        },
    }
    async with httpx.AsyncClient(timeout=10, headers={"x-api-key": creds["api_key"]}) as c:
        (await c.post(f"{host}/api/v1/runs", json=run)).raise_for_status()


async def _send_braintrust(trace: AgentRunTrace, config: Dict) -> None:
    creds = config["credentials"]
    host = (config.get("endpoint_url") or "https://api.braintrust.dev").rstrip("/")
    project_id = config.get("remote_project_id", "")
    end = datetime.now(timezone.utc)
    start = end - timedelta(milliseconds=trace.total_latency_ms)

    span = {
        "id": trace.session_id,
        "project_id": project_id,
        "log_id": "production",
        "span_attributes": {"name": f"agent-run/{trace.route}", "type": "chain"},
        "input": trace.task,
        "output": trace.final_answer,
        "metrics": {
            "start": start.timestamp(),
            "end": end.timestamp(),
            "tokens": trace.total_tokens,
        },
        "metadata": {
            "route": trace.route,
            "status": trace.status,
            "tools_used": trace.tool_names_used,
            "steps": len(trace.steps),
        },
    }
    async with httpx.AsyncClient(timeout=10, headers={"Authorization": f"Bearer {creds['api_key']}"}) as c:
        (await c.post(f"{host}/v1/project_logs/{project_id}/insert", json={"events": [span]})).raise_for_status()
