"""Redis-backed session store for agent runs.

Each session tracks the full execution history so the agent gateway can:
  - Resume a paused session
  - Stream partial results to the client
  - Enforce per-session token budgets
  - Surface complete traces to observability backends
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

import redis as redis_lib

from app.config import settings

_redis = redis_lib.from_url(settings.redis_url, decode_responses=True)


class SessionStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    budget_exceeded = "budget_exceeded"


def create_session(session_id: str, task: str, route: str, metadata: Dict) -> Dict[str, Any]:
    session = {
        "session_id": session_id,
        "task": task,
        "route": route,
        "status": SessionStatus.pending,
        "steps": [],
        "total_tokens": 0,
        "final_answer": None,
        "error": None,
        "metadata": metadata,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save(session_id, session)
    return session


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    raw = _redis.get(_key(session_id))
    return json.loads(raw) if raw else None


def update_status(session_id: str, status: SessionStatus) -> None:
    session = get_session(session_id)
    if session:
        session["status"] = status
        session["updated_at"] = datetime.now(timezone.utc).isoformat()
        _save(session_id, session)


def append_step(session_id: str, step: Dict[str, Any]) -> None:
    session = get_session(session_id)
    if session:
        session["steps"].append(step)
        session["total_tokens"] += step.get("tokens_used", 0)
        session["updated_at"] = datetime.now(timezone.utc).isoformat()
        _save(session_id, session)


def finalize(session_id: str, answer: str, status: SessionStatus) -> None:
    session = get_session(session_id)
    if session:
        session["final_answer"] = answer
        session["status"] = status
        session["updated_at"] = datetime.now(timezone.utc).isoformat()
        _save(session_id, session)


def _save(session_id: str, session: Dict) -> None:
    _redis.setex(_key(session_id), settings.session_ttl_seconds, json.dumps(session))


def _key(session_id: str) -> str:
    return f"agent_session:{session_id}"
