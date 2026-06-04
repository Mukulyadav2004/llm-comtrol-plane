"""Config store for MCP Gateway — polls control plane for MCP server list + observability configs."""
import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

log = logging.getLogger(__name__)

_config: Dict[str, Any] = {"version": 0, "servers": [], "observability_configs": []}


def get_server(name: str) -> Optional[Dict[str, Any]]:
    for s in _config["servers"]:
        if s["name"] == name and s.get("healthy", True):
            return s
    return None


def find_server_for_tool(tool_name: str) -> Optional[Dict[str, Any]]:
    for s in _config["servers"]:
        if tool_name in s.get("capabilities", []) and s.get("healthy", True):
            return s
    return None


def list_tools() -> List[Dict[str, Any]]:
    tools = []
    for s in _config["servers"]:
        if not s.get("healthy", True):
            continue
        for cap in s.get("capabilities", []):
            tools.append({
                "tool": cap,
                "server": s["name"],
                "endpoint": s["endpoint_url"],
                "tags": s.get("tags", {}),
            })
    return tools


def get_observability_configs() -> List[Dict[str, Any]]:
    return _config.get("observability_configs", [])


async def refresh_config() -> None:
    global _config
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            mcp_resp = await client.get(f"{settings.control_plane_url}/config/mcp")
            mcp_resp.raise_for_status()
            mcp_data = mcp_resp.json()

            gw_resp = await client.get(f"{settings.control_plane_url}/config/gateway")
            gw_resp.raise_for_status()
            gw_data = gw_resp.json()

        _config = {
            "version": mcp_data.get("version", 0),
            "servers": mcp_data.get("servers", []),
            "observability_configs": gw_data.get("observability_configs", []),
        }
        log.info("mcp_gateway.config_refreshed", servers=len(_config["servers"]))
    except Exception:
        log.exception("mcp_gateway.config_refresh_failed")


async def poll_config() -> None:
    while True:
        await asyncio.sleep(settings.config_poll_interval)
        await refresh_config()
