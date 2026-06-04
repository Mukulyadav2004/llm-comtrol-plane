"""Proxy tool calls to the appropriate MCP server fetched from the control plane."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

log = logging.getLogger(__name__)

_servers_cache: List[Dict[str, Any]] = []


async def refresh_servers() -> None:
    global _servers_cache
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{settings.control_plane_url}/config/mcp")
            resp.raise_for_status()
            _servers_cache = resp.json().get("servers", [])
            log.info("mcp_proxy.servers_refreshed", count=len(_servers_cache))
    except Exception:
        log.exception("mcp_proxy.refresh_failed")


def find_server_for_tool(tool_name: str) -> Optional[Dict[str, Any]]:
    for server in _servers_cache:
        if tool_name in server.get("capabilities", []):
            return server
    return None


def list_all_tools() -> List[Dict[str, Any]]:
    tools = []
    for server in _servers_cache:
        for cap in server.get("capabilities", []):
            tools.append({"tool": cap, "server": server["name"], "endpoint": server["endpoint_url"]})
    return tools


async def call_tool(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    server = find_server_for_tool(tool_name)
    if not server:
        raise ValueError(f"No MCP server registered for tool '{tool_name}'")

    headers = {}
    if server.get("auth_header"):
        headers["Authorization"] = server["auth_header"]

    payload = {"tool": tool_name, "arguments": arguments}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{server['endpoint_url']}/tools/call", json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()
