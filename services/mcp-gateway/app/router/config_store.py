"""Config store for MCP Gateway.

Two layers of state:
  1. Control-plane config — the list of registered MCP servers (+ guardrails and
     observability configs), polled from the control plane.
  2. Discovered tool catalog — for each healthy server we call its `/tools`
     endpoint to pull rich tool metadata (description, input_schema, keywords,
     tags). This is real MCP-style discovery and powers intelligent routing,
     argument extraction and the enriched `/v1/tools` listing.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

log = logging.getLogger(__name__)

_config: Dict[str, Any] = {
    "version": 0,
    "servers": [],
    "observability_configs": [],
    "guardrails": [],
}

# tool_name -> metadata dict {tool, server, endpoint, description, input_schema, keywords, tags}
_catalog: Dict[str, Dict[str, Any]] = {}


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


def get_tool_meta(tool_name: str) -> Optional[Dict[str, Any]]:
    return _catalog.get(tool_name)


def get_tool_catalog() -> List[Dict[str, Any]]:
    return list(_catalog.values())


def list_tools() -> List[Dict[str, Any]]:
    """Enriched listing — catalog metadata when discovered, capability fallback otherwise."""
    if _catalog:
        return [
            {
                "tool": m["tool"],
                "server": m["server"],
                "endpoint": m["endpoint"],
                "description": m.get("description", ""),
                "input_schema": m.get("input_schema", {}),
                "tags": m.get("tags", {}),
            }
            for m in _catalog.values()
        ]
    # Fallback: control-plane capabilities only (no discovery yet)
    tools = []
    for s in _config["servers"]:
        if not s.get("healthy", True):
            continue
        for cap in s.get("capabilities", []):
            tools.append({"tool": cap, "server": s["name"], "endpoint": s["endpoint_url"], "tags": s.get("tags", {})})
    return tools


def get_observability_configs() -> List[Dict[str, Any]]:
    return _config.get("observability_configs", [])


def get_guardrails() -> List[Dict[str, Any]]:
    return _config.get("guardrails", [])


async def _discover_tools(servers: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Pull each healthy server's tool catalog. Falls back to capability names."""
    catalog: Dict[str, Dict[str, Any]] = {}

    async def _fetch(server: Dict[str, Any]) -> None:
        if not server.get("healthy", True):
            return
        endpoint = server["endpoint_url"]
        server_tags = server.get("tags", {})
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{endpoint}/tools")
                resp.raise_for_status()
                for t in resp.json().get("tools", []):
                    catalog[t["name"]] = {
                        "tool": t["name"],
                        "server": server["name"],
                        "endpoint": endpoint,
                        "description": t.get("description", ""),
                        "input_schema": t.get("input_schema", {}),
                        "keywords": t.get("keywords", []),
                        "tags": {**server_tags, **t.get("tags", {})},
                    }
        except Exception:
            # Discovery is best-effort — fall back to declared capabilities.
            log.warning("mcp_gateway.discovery_failed", server=server["name"])
            for cap in server.get("capabilities", []):
                catalog.setdefault(cap, {
                    "tool": cap, "server": server["name"], "endpoint": endpoint,
                    "description": "", "input_schema": {}, "keywords": [], "tags": server_tags,
                })

    await asyncio.gather(*[_fetch(s) for s in servers], return_exceptions=True)
    return catalog


async def refresh_config() -> None:
    global _config, _catalog
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            mcp_resp = await client.get(f"{settings.control_plane_url}/config/mcp")
            mcp_resp.raise_for_status()
            mcp_data = mcp_resp.json()

            gw_resp = await client.get(f"{settings.control_plane_url}/config/gateway")
            gw_resp.raise_for_status()
            gw_data = gw_resp.json()

        servers = mcp_data.get("servers", [])
        _config = {
            "version": mcp_data.get("version", 0),
            "servers": servers,
            "observability_configs": gw_data.get("observability_configs", []),
            "guardrails": gw_data.get("guardrails", []),
        }
        _catalog = await _discover_tools(servers)
        log.info("mcp_gateway.config_refreshed", servers=len(servers), tools=len(_catalog))
    except Exception:
        log.exception("mcp_gateway.config_refresh_failed")


async def poll_config() -> None:
    while True:
        await asyncio.sleep(settings.config_poll_interval)
        await refresh_config()
