"""MCP Tool Server.

Implements the minimal HTTP contract the MCP Registry and MCP Gateway speak:
  GET  /health                  -> liveness + advertised tool names
  GET  /tools                   -> full catalog (name, description, input_schema, tags)
  GET  /tools/{tool}/schema     -> input schema for one tool
  POST /tools/call {tool, arguments} -> {"result": ...}

One image serves a configurable TOOLSET so it can be deployed several times to
demonstrate routing across distinct MCP servers.
"""
import structlog
from fastapi import FastAPI, HTTPException
from prometheus_client import Counter, make_asgi_app
from pydantic import BaseModel
from typing import Any, Dict

from app.config import settings
from app.tools import REGISTRY

log = structlog.get_logger()

CALLS = Counter("mcp_tools_calls_total", "Tool calls handled", ["tool", "status"])

app = FastAPI(
    title=f"{settings.app_name} ({settings.toolset})",
    description="A registerable MCP tool server exposing callable tools over HTTP.",
    version="1.0.0",
)

app.mount("/metrics", make_asgi_app())


def _toolset():
    return REGISTRY.all(settings.toolset)


class ToolCallRequest(BaseModel):
    tool: str
    arguments: Dict[str, Any] = {}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": settings.app_name,
        "toolset": settings.toolset,
        "tools": [t.name for t in _toolset()],
    }


@app.get("/tools")
async def list_tools():
    return {"tools": [t.public() for t in _toolset()]}


@app.get("/tools/{tool}/schema")
async def tool_schema(tool: str):
    t = REGISTRY.get(tool)
    if not t or t not in _toolset():
        raise HTTPException(status_code=404, detail=f"Tool '{tool}' not served here")
    return {"name": t.name, "description": t.description, "input_schema": t.input_schema}


@app.post("/tools/call")
async def call_tool(req: ToolCallRequest):
    t = REGISTRY.get(req.tool)
    if not t or t not in _toolset():
        CALLS.labels(req.tool, "not_found").inc()
        raise HTTPException(status_code=404, detail=f"Tool '{req.tool}' not served here")
    try:
        result = await t.invoke(req.arguments)
        CALLS.labels(req.tool, "ok").inc()
        return {"result": result}
    except ValueError as exc:
        CALLS.labels(req.tool, "bad_request").inc()
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        CALLS.labels(req.tool, "error").inc()
        log.exception("tool.call_failed", tool=req.tool)
        raise HTTPException(status_code=500, detail=str(exc))


@app.on_event("startup")
async def startup():
    log.info("mcp_tools.startup", toolset=settings.toolset, tools=[t.name for t in _toolset()])
