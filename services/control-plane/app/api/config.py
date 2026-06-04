"""Config distribution API — gateway polls this to get its current routing config."""
import time
from typing import Any, Dict

from fastapi import APIRouter, Body

from app.context.loader import invalidate
from app.templates.renderer import render_gateway_config, render_mcp_registry

router = APIRouter(tags=["config"])

_version_counter = int(time.time())


def _next_version() -> int:
    global _version_counter
    _version_counter += 1
    return _version_counter


@router.get("/config/gateway")
async def get_gateway_config() -> Dict[str, Any]:
    """Gateway polls this on startup and after reload signals."""
    return render_gateway_config(version=_version_counter)


@router.get("/config/mcp")
async def get_mcp_config() -> Dict[str, Any]:
    return render_mcp_registry(version=_version_counter)


@router.post("/internal/reload")
async def trigger_reload(body: Dict[str, Any] = Body(...)) -> Dict[str, str]:
    """Called by the broker worker after a successful provisioning task."""
    resource_type = body.get("resource_type", "")
    _next_version()
    if resource_type:
        invalidate(resource_type)
    return {"status": "reload_triggered", "version": str(_version_counter)}


@router.get("/internal/context")
async def get_raw_context() -> Dict[str, Any]:
    """Debug: see the raw context the control plane is working with."""
    from app.context.loader import build_full_context
    return build_full_context()
