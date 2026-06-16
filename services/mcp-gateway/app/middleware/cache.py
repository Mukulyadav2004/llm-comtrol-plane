"""Optional result cache for idempotent tool calls.

A tool opts in via its tags: `cacheable: "true"` (and optional `cache_ttl`
seconds). Keyed by tool name + a hash of the arguments, stored in Redis. Saves
repeat work for deterministic tools (calculator, unit_convert, wikipedia, ...).
"""
import hashlib
import json
import logging
from typing import Any, Dict, Optional

import redis as redis_lib

from app.config import settings

log = logging.getLogger(__name__)

_redis = redis_lib.from_url(settings.redis_url, decode_responses=True)

_DEFAULT_TTL = 300


def _is_cacheable(tags: Dict[str, Any]) -> bool:
    return str(tags.get("cacheable", "")).lower() == "true"


def _ttl(tags: Dict[str, Any]) -> int:
    try:
        return int(tags.get("cache_ttl", _DEFAULT_TTL))
    except (TypeError, ValueError):
        return _DEFAULT_TTL


def _key(tool: str, arguments: Dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(arguments, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return f"mcp_cache:{tool}:{digest}"


def get_cached(tool: str, arguments: Dict[str, Any], tags: Dict[str, Any]) -> Optional[Any]:
    if not _is_cacheable(tags):
        return None
    try:
        raw = _redis.get(_key(tool, arguments))
        return json.loads(raw) if raw else None
    except Exception:
        return None


def set_cached(tool: str, arguments: Dict[str, Any], tags: Dict[str, Any], result: Any) -> None:
    if not _is_cacheable(tags):
        return
    try:
        _redis.setex(_key(tool, arguments), _ttl(tags), json.dumps(result, default=str))
    except Exception:
        log.warning("mcp_cache.set_failed", tool=tool)
