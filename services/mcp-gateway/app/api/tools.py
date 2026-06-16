"""MCP Gateway — authenticated, rate-limited, guarded, observable tool API.

Endpoints:
  GET  /v1/tools              list discovered tools (name, description, schema)
  POST /v1/tools/call         call a named tool (guardrails + cache + rate limit + trace)
  POST /v1/tools/route        intelligently pick the best tool for a request (no execution)
  POST /v1/tools/execute      route + extract arguments + call, in one shot
  GET  /v1/tools/{tool}/schema  fetch a tool's input schema
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.middleware.cache import get_cached, set_cached
from app.middleware.guardrails import (
    GuardrailViolation,
    apply_input_guardrails,
    apply_output_guardrails,
)
from app.middleware.observability import ToolCallTrace, dispatch_tool_trace
from app.middleware.rate_limit import check_tool_rate_limit, resolve_rpm
from app.router import tool_router
from app.router.config_store import (
    find_server_for_tool,
    get_guardrails,
    get_observability_configs,
    get_tool_catalog,
    get_tool_meta,
    list_tools,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/tools", tags=["tools"])

_MAX_ATTEMPTS = 2  # one retry on transient upstream failure


class ToolCallRequest(BaseModel):
    tool: str
    arguments: Dict[str, Any] = {}
    caller_id: str = "anonymous"
    trace_id: Optional[str] = None  # parent trace (e.g. agent session) for end-to-end nesting


class ToolCallResponse(BaseModel):
    trace_id: str
    tool: str
    server: str
    result: Any
    latency_ms: float
    success: bool
    cached: bool = False
    error: str | None = None


class RouteRequest(BaseModel):
    query: str


class ExecuteRequest(BaseModel):
    query: str
    caller_id: str = "anonymous"
    trace_id: Optional[str] = None
    arguments: Optional[Dict[str, Any]] = None  # skip LLM argument extraction if provided


@router.get("", summary="List all available tools across registered MCP servers")
async def list_available_tools() -> Dict[str, List]:
    return {"tools": list_tools()}


@router.post("/call", response_model=ToolCallResponse)
async def call_tool(req: ToolCallRequest, request: Request) -> ToolCallResponse:
    client_id = req.caller_id or (request.client.host if request.client else "unknown")
    return await _execute(req.tool, req.arguments, client_id, req.trace_id)


@router.post("/route", summary="Intelligently select the best tool for a natural-language request")
async def route_tool(req: RouteRequest) -> Dict[str, Any]:
    return await tool_router.select_tool(req.query, get_tool_catalog())


@router.post("/execute", summary="Route to the best tool, fill its arguments, and call it")
async def execute_tool(req: ExecuteRequest, request: Request) -> Dict[str, Any]:
    client_id = req.caller_id or (request.client.host if request.client else "unknown")

    selection = await tool_router.select_tool(req.query, get_tool_catalog())
    tool_name = selection.get("tool")
    if not tool_name:
        raise HTTPException(status_code=404, detail="No suitable tool found for request")

    meta = get_tool_meta(tool_name) or {"tool": tool_name, "input_schema": {}}
    arguments = req.arguments if req.arguments is not None else await tool_router.extract_arguments(req.query, meta)

    call = await _execute(tool_name, arguments, client_id, req.trace_id)
    return {"routing": selection, "arguments": arguments, **call.model_dump()}


@router.get("/{tool}/schema", summary="Get the input schema for a specific tool")
async def get_tool_schema(tool: str) -> Dict[str, Any]:
    meta = get_tool_meta(tool)
    if meta and meta.get("input_schema"):
        return {"name": tool, "description": meta.get("description", ""), "input_schema": meta["input_schema"]}
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


# ── orchestration ─────────────────────────────────────────────────────────────
async def _execute(tool: str, arguments: Dict[str, Any], client_id: str, parent_trace_id: Optional[str]) -> ToolCallResponse:
    meta = get_tool_meta(tool)
    tags = (meta or {}).get("tags", {})

    # 1. Rate limit (per tool, per caller; tool-specific RPM via tags).
    if not check_tool_rate_limit(tool, client_id, rpm=resolve_rpm(tags)):
        raise HTTPException(status_code=429, detail=f"Rate limit exceeded for tool '{tool}'")

    server = find_server_for_tool(tool)
    if not server:
        raise HTTPException(status_code=404, detail=f"No healthy MCP server registered for tool '{tool}'")

    guardrails = get_guardrails()

    # 2. Input guardrails (may redact arguments or block the call).
    try:
        arguments = apply_input_guardrails(arguments, guardrails)
    except GuardrailViolation as gv:
        log.warning("mcp_gateway.input_blocked", tool=tool, guardrail=gv.guardrail_name)
        raise HTTPException(status_code=422, detail=f"Guardrail '{gv.guardrail_name}' blocked input: {gv.detail}")

    trace_id = str(uuid.uuid4())

    # 3. Cache hit?
    cached = get_cached(tool, arguments, tags)
    if cached is not None:
        return ToolCallResponse(
            trace_id=trace_id, tool=tool, server=server["name"], result=cached,
            latency_ms=0.0, success=True, cached=True,
        )

    # 4. Call the MCP server (with one retry on transient failure).
    start = time.monotonic()
    result, error, success = await _forward(server, tool, arguments)
    latency_ms = (time.monotonic() - start) * 1000

    # 5. Output guardrails (redact PII/secrets leaking back from a tool).
    if success and result is not None:
        try:
            result = apply_output_guardrails(result, guardrails)
        except GuardrailViolation as gv:
            success, error, result = False, f"Output blocked by guardrail '{gv.guardrail_name}'", None

    if success:
        set_cached(tool, arguments, tags, result)

    # 6. Fire-and-forget observability.
    obs_configs = get_observability_configs()
    if obs_configs:
        trace = ToolCallTrace(
            trace_id=trace_id, parent_trace_id=parent_trace_id, tool_name=tool,
            server_name=server["name"], caller_id=client_id, arguments=arguments,
            result=result, error=error, latency_ms=latency_ms, success=success,
        )
        asyncio.create_task(dispatch_tool_trace(trace, obs_configs))

    if not success:
        raise HTTPException(status_code=502, detail=error)

    return ToolCallResponse(
        trace_id=trace_id, tool=tool, server=server["name"], result=result,
        latency_ms=round(latency_ms, 2), success=True,
    )


async def _forward(server: Dict[str, Any], tool: str, arguments: Dict[str, Any]):
    """POST the call to the MCP server. Returns (result, error, success)."""
    headers = {}
    if server.get("auth_header"):
        headers["Authorization"] = server["auth_header"]

    last_error = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{server['endpoint_url']}/tools/call",
                    json={"tool": tool, "arguments": arguments},
                    headers=headers,
                )
                resp.raise_for_status()
                return resp.json(), None, True
        except httpx.HTTPStatusError as exc:
            # 4xx is the tool's verdict on the request — don't retry.
            last_error = f"MCP server returned {exc.response.status_code}: {exc.response.text[:200]}"
            if exc.response.status_code < 500:
                break
        except Exception as exc:
            last_error = str(exc)
        if attempt < _MAX_ATTEMPTS:
            await asyncio.sleep(0.25 * attempt)

    log.warning("mcp_gateway.tool_error", tool=tool, server=server["name"], error=last_error)
    return None, last_error, False
