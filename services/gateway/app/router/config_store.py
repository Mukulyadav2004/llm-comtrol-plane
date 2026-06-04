"""In-memory config store — populated by polling the control plane.

The gateway calls /config/gateway on startup and every poll interval,
then caches the result here so request handling has zero network overhead.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

log = logging.getLogger(__name__)

_config: Dict[str, Any] = {"version": 0, "routes": [], "guardrails": [], "mcp_servers": []}


def get_route(name: str) -> Optional[Dict[str, Any]]:
    for route in _config["routes"]:
        if route["name"] == name and route.get("enabled", True):
            return route
    return None


def list_routes() -> List[Dict[str, Any]]:
    return _config["routes"]


def get_guardrail(guardrail_id: str) -> Optional[Dict[str, Any]]:
    for g in _config["guardrails"]:
        if g["id"] == guardrail_id or g["name"] == guardrail_id:
            return g
    return None


def get_all_guardrails_for_route(route: Dict[str, Any]) -> List[Dict[str, Any]]:
    ids = set(route.get("guardrail_ids", []))
    return [g for g in _config["guardrails"] if g["id"] in ids or g["name"] in ids]


def get_semantic_rules() -> List[Dict[str, Any]]:
    return _config.get("semantic_rules", [])


def get_observability_configs() -> List[Dict[str, Any]]:
    return _config.get("observability_configs", [])


def current_version() -> int:
    return _config.get("version", 0)


async def poll_config():
    while True:
        await asyncio.sleep(settings.config_poll_interval)
        await refresh_config()


async def refresh_config():
    global _config
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{settings.control_plane_url}/config/gateway")
            resp.raise_for_status()
            new_cfg = resp.json()
            if new_cfg.get("version", 0) != _config.get("version", 0):
                _config = new_cfg
                log.info("gateway.config_updated", version=_config["version"])
    except Exception:
        log.exception("gateway.config_poll_failed")
