"""Config store for Agent Gateway — pulls observability configs from control plane."""
import asyncio
import logging
from typing import Any, Dict, List

import httpx
from app.config import settings

log = logging.getLogger(__name__)
_obs_configs: List[Dict[str, Any]] = []


def get_observability_configs() -> List[Dict[str, Any]]:
    return _obs_configs


async def refresh_config() -> None:
    global _obs_configs
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{settings.control_plane_url}/config/gateway")
            resp.raise_for_status()
            _obs_configs = resp.json().get("observability_configs", [])
            log.info("agent_gateway.config_refreshed", obs_backends=len(_obs_configs))
    except Exception:
        log.exception("agent_gateway.config_refresh_failed")


async def poll_config() -> None:
    while True:
        await asyncio.sleep(settings.config_poll_interval)
        await refresh_config()
