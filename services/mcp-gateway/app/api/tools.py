"""MCP Gateway — authenticated, rate-limited, observable tool call API."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.middleware.observability import ToolCallTrace, dispatch_tool_trace
from app.middleware.rate_limit import check_tool_rate_limit
from app.router.config_store import find_server_for_tool, get_observability_configs, list_tools

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/tools", tags=["tools"])


class ToolCallRequest(BaseModel):
    tool: str
    arguments: Dict[str, Any] = {}
    caller_id: str = "anonymous"
    # Parent trace id (e.g. an agent session) so this tool call nests under the
    # same end-to-end trace as the agent run and its LLM calls.
    trace_id: Optional[str] = None


class ToolCallResponse(BaseModel):
    trace_id: str
    tool: str
    server: str
    result: Any
    latency_ms: float
    success: bool
    error: str | None = None


@router.get("", summary="List all available tools across registered MCP servers")
async def list_available_tools() -> Dict[str, List]:
    return {"tools": list_tools()}


@router.post("/call", response_model=ToolCallResponse)
async def call_tool(req: ToolCallRequest, request: Request) -> ToolCallResponse:
    client_id = req.caller_id or (request.client.host if request.client else "unknown")

    # Rate limit per tool per caller
    if not check_tool_rate_limit(req.tool, client_id):
        raise HTTPException(status_code=429, detail=f"Rate limit exceeded for tool '{req.tool}'")

    server = find_server_for_tool(req.tool)
    if not server:
        raise HTTPException(status_code=404, detail=f"No healthy MCP server registered for tool '{req.tool}'")

    trace_id = str(uuid.uuid4())
    start = time.monotonic()
    result = None
    error = None
    success = False

    try:
        headers = {}
        if server.get("auth_header"):
            headers["Authorization"] = server["auth_header"]

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{server['endpoint_url']}/tools/call",
                json={"tool": req.tool, "arguments": req.arguments},
                headers=headers,
            )
            resp.raise_for_status()
            result = resp.json()
            success = True

    except httpx.HTTPStatusError as exc:
        error = f"MCP server returned {exc.response.status_code}"
        log.warning("mcp_gateway.tool_error", tool=req.tool, server=server["name"], error=error)
    except Exception as exc:
        error = str(exc)
        log.exception("mcp_gateway.tool_exception", tool=req.tool)

    latency_ms = (time.monotonic() - start) * 1000

    # Fire-and-forget observability
    obs_configs = get_observability_configs()
    if obs_configs:
        trace = ToolCallTrace(
            trace_id=trace_id,
            parent_trace_id=req.trace_id,
            tool_name=req.tool,
            server_name=server["name"],
            caller_id=client_id,
            arguments=req.arguments,
            result=result,
            error=error,
            latency_ms=latency_ms,
            success=success,
        )
        asyncio.create_task(dispatch_tool_trace(trace, obs_configs))

    if not success:
        raise HTTPException(status_code=502, detail=error)

    return ToolCallResponse(
        trace_id=trace_id,
        tool=req.tool,
        server=server["name"],
        result=result,
        latency_ms=round(latency_ms, 2),
        success=success,
    )


@router.get("/{tool}/schema", summary="Get the input schema for a specific tool")
async def get_tool_schema(tool: str) -> Dict[str, Any]:
    server = find_server_for_tool(tool)
    if not server:
        raise HTTPException(status_code=404, detail=f"Tool '{tool}' not found")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{server['endpoint_url']}/tools/{tool}/schema")
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch schema: {exc}")
