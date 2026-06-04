"""Pulls live context (routes, MCP servers, guardrails) from the Broker and caches in Redis."""
import json
import logging
from typing import Any, Dict, List

import httpx
import redis as redis_lib

from app.config import settings

log = logging.getLogger(__name__)

_redis = redis_lib.from_url(settings.redis_url, decode_responses=True)

_CACHE_TTL = settings.context_refresh_interval * 2  # keep cache slightly longer than refresh cycle


def fetch_routes() -> List[Dict[str, Any]]:
    return _cached_fetch("ctx:routes", f"{settings.broker_url}/v2/routes")


def fetch_mcp_servers() -> List[Dict[str, Any]]:
    return _cached_fetch("ctx:mcp_servers", f"{settings.broker_url}/v2/mcp-servers")


def fetch_guardrails() -> List[Dict[str, Any]]:
    return _cached_fetch("ctx:guardrails", f"{settings.broker_url}/v2/guardrails")


def fetch_semantic_rules() -> List[Dict[str, Any]]:
    return _cached_fetch("ctx:semantic_rules", f"{settings.broker_url}/v2/semantic-routes")


def fetch_observability_configs() -> List[Dict[str, Any]]:
    return _cached_fetch("ctx:observability_configs", f"{settings.broker_url}/v2/observability")


def build_full_context() -> Dict[str, Any]:
    return {
        "routes": fetch_routes(),
        "mcp_servers": fetch_mcp_servers(),
        "guardrails": fetch_guardrails(),
        "semantic_rules": fetch_semantic_rules(),
        "observability_configs": fetch_observability_configs(),
    }


def invalidate(resource_type: str) -> None:
    key_map = {
        "route": "ctx:routes",
        "mcp_server": "ctx:mcp_servers",
        "guardrail": "ctx:guardrails",
        "semantic_rule": "ctx:semantic_rules",
    }
    key = key_map.get(resource_type, f"ctx:{resource_type}s")
    _redis.delete(key)
    log.info("context.invalidated", key=key)


def _cached_fetch(cache_key: str, url: str) -> List[Dict[str, Any]]:
    cached = _redis.get(cache_key)
    if cached:
        return json.loads(cached)
    data = _fetch_from_broker(url)
    _redis.setex(cache_key, _CACHE_TTL, json.dumps(data))
    return data


def _fetch_from_broker(url: str) -> List[Dict[str, Any]]:
    try:
        with httpx.Client(timeout=5) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.json()
    except Exception:
        log.exception("context.fetch_failed", url=url)
        return []
