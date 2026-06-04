from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.proxy.mcp_proxy import call_tool, list_all_tools, refresh_servers

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolCallRequest(BaseModel):
    tool: str
    arguments: Dict[str, Any] = {}


@router.get("", summary="List all available tools across registered MCP servers")
async def list_tools() -> Dict[str, List]:
    return {"tools": list_all_tools()}


@router.post("/call", summary="Proxy a tool call to the appropriate MCP server")
async def invoke_tool(req: ToolCallRequest) -> Dict[str, Any]:
    try:
        result = await call_tool(req.tool, req.arguments)
        return {"tool": req.tool, "result": result}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Tool call failed: {exc}")


@router.post("/refresh", summary="Reload MCP server list from control plane")
async def refresh():
    await refresh_servers()
    return {"status": "refreshed", "count": len(list_all_tools())}
