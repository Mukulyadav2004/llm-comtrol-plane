"""Agent Gateway API.

POST /v1/agents/run        — submit a task, get session_id back immediately
GET  /v1/agents/{id}       — poll session status + steps
GET  /v1/agents/{id}/stream — SSE stream of steps as they happen
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.agents import react_agent
from app.config import settings
from app.middleware.observability import AgentRunTrace, dispatch_agent_trace
from app.router.config_store import get_observability_configs
from app.router.session_store import (
    SessionStatus,
    append_step,
    create_session,
    finalize,
    get_session,
    update_status,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/agents", tags=["agents"])


class AgentRunRequest(BaseModel):
    task: str
    route: str = "local-llama"
    max_steps: int = settings.max_agent_steps
    max_tokens: int = settings.max_session_tokens
    metadata: Dict[str, Any] = {}


class AgentRunResponse(BaseModel):
    session_id: str
    status: str
    message: str


@router.post("/run", response_model=AgentRunResponse, status_code=202)
async def run_agent(req: AgentRunRequest, background_tasks: BackgroundTasks):
    session_id = str(uuid.uuid4())
    create_session(session_id, req.task, req.route, req.metadata)
    background_tasks.add_task(_execute_agent, session_id, req)
    return AgentRunResponse(
        session_id=session_id,
        status=SessionStatus.pending,
        message="Agent session started. Poll /v1/agents/{session_id} for status.",
    )


@router.get("/{session_id}")
async def get_agent_status(session_id: str) -> Dict[str, Any]:
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.get("/{session_id}/stream")
async def stream_agent(session_id: str, request: Request):
    """SSE endpoint — streams each step as it is produced by the agent loop."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    async def event_generator():
        seen_steps = 0
        while True:
            if await request.is_disconnected():
                break
            session = get_session(session_id)
            if not session:
                break
            steps = session.get("steps", [])
            for step in steps[seen_steps:]:
                yield {"event": "step", "data": json.dumps(step)}
                seen_steps += 1
            status = session.get("status")
            if status in (SessionStatus.completed, SessionStatus.failed, SessionStatus.budget_exceeded):
                yield {"event": "done", "data": json.dumps({
                    "status": status,
                    "final_answer": session.get("final_answer"),
                })}
                break
            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())


async def _execute_agent(session_id: str, req: AgentRunRequest) -> None:
    update_status(session_id, SessionStatus.running)
    start = time.monotonic()
    steps_collected: List[Dict] = []

    async def on_step(step: Dict) -> None:
        append_step(session_id, step)
        steps_collected.append(step)

    try:
        answer, steps = await react_agent.run(
            session_id=session_id,
            task=req.task,
            route=req.route,
            max_steps=req.max_steps,
            max_tokens=req.max_tokens,
            on_step=on_step,
        )

        budget_hit = answer.startswith("[Budget") or answer.startswith("[Max steps")
        final_status = SessionStatus.budget_exceeded if budget_hit else SessionStatus.completed
        finalize(session_id, answer, final_status)

    except Exception as exc:
        log.exception("agent.run_failed", session_id=session_id)
        finalize(session_id, str(exc), SessionStatus.failed)
        final_status = SessionStatus.failed
        answer = str(exc)

    total_latency_ms = (time.monotonic() - start) * 1000

    # Fire-and-forget observability trace
    obs_configs = get_observability_configs()
    if obs_configs:
        session = get_session(session_id)
        think_steps = [s for s in steps_collected if s["type"] == "think"]
        act_steps = [s for s in steps_collected if s["type"] == "act"]
        tool_names = list({s["tool"] for s in act_steps if "tool" in s})

        trace = AgentRunTrace(
            session_id=session_id,
            task=req.task,
            route=req.route,
            final_answer=answer,
            status=final_status,
            steps=steps_collected,
            total_tokens=session.get("total_tokens", 0) if session else 0,
            total_latency_ms=total_latency_ms,
            think_steps=len(think_steps),
            act_steps=len(act_steps),
            tool_names_used=tool_names,
            metadata=req.metadata,
        )
        asyncio.create_task(dispatch_agent_trace(trace, obs_configs))
