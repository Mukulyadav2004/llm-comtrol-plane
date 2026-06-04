"""ReAct agent loop (Reason + Act).

Pattern from "ReAct: Synergizing Reasoning and Acting in Language Models" (Yao et al., 2022).

Each step:
  1. THINK  — ask LLM Gateway: given goal + history, what should I do next?
  2. ACT    — if LLM emits a tool call, send it to MCP Gateway
  3. OBSERVE — feed tool result back as context
  4. Repeat until LLM emits a FINAL ANSWER or max_steps is reached.

The LLM's response must follow this format (enforced via system prompt):
  Thought: <reasoning>
  Action: <tool_name>(<json_args>)   ← tool call
  OR
  Final Answer: <answer>             ← done

This keeps the agent fully observable — every thought, action, and observation
is recorded as a step in the session and sent to observability backends.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx

from app.config import settings

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a helpful AI assistant with access to tools.
To use a tool, respond with EXACTLY this format:
  Thought: <your reasoning>
  Action: tool_name({"arg1": "value1"})

When you have enough information to answer, respond with EXACTLY:
  Final Answer: <your complete answer>

Available tools: {tools}

Rules:
- Always think before acting.
- Use tools only when necessary.
- Never make up tool results.
- Be concise in your reasoning."""

_ACTION_RE = re.compile(r"Action:\s*(\w+)\((\{.*?\})\)", re.DOTALL)
_FINAL_RE = re.compile(r"Final Answer:\s*(.+)", re.DOTALL)


async def run(
    session_id: str,
    task: str,
    route: str,
    max_steps: int,
    max_tokens: int,
    on_step,  # async callback(step_dict) for streaming
) -> Tuple[str, List[Dict]]:
    """Execute the ReAct loop. Returns (final_answer, steps)."""
    tools = await _fetch_available_tools()
    tool_names = [t["tool"] for t in tools]

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT.format(tools=", ".join(tool_names) or "none")},
        {"role": "user", "content": task},
    ]

    steps = []
    total_tokens = 0

    for step_num in range(1, max_steps + 1):
        step_start = time.monotonic()

        # --- THINK: call LLM Gateway ---
        llm_result = await _call_llm(route, messages, session_id)
        content = llm_result["choices"][0]["message"]["content"]
        usage = llm_result.get("usage", {})
        step_tokens = usage.get("total_tokens", 0)
        total_tokens += step_tokens

        think_step = {
            "step": step_num,
            "type": "think",
            "content": content,
            "tokens_used": step_tokens,
            "latency_ms": round((time.monotonic() - step_start) * 1000, 2),
        }
        steps.append(think_step)
        await on_step(think_step)

        # --- Check for Final Answer ---
        final_match = _FINAL_RE.search(content)
        if final_match:
            answer = final_match.group(1).strip()
            return answer, steps

        # --- ACT: parse and execute tool call ---
        action_match = _ACTION_RE.search(content)
        if not action_match:
            # LLM neither called a tool nor gave a final answer — prompt it
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": "Please either call a tool or provide a Final Answer."})
            continue

        tool_name = action_match.group(1)
        try:
            tool_args = json.loads(action_match.group(2))
        except json.JSONDecodeError:
            tool_args = {}

        act_start = time.monotonic()
        tool_result, tool_error = await _call_tool(session_id, tool_name, tool_args)
        act_latency = round((time.monotonic() - act_start) * 1000, 2)

        observe_content = f"Observation: {json.dumps(tool_result) if tool_result else f'Error: {tool_error}'}"

        act_step = {
            "step": step_num,
            "type": "act",
            "tool": tool_name,
            "arguments": tool_args,
            "observation": tool_result,
            "error": tool_error,
            "latency_ms": act_latency,
            "tokens_used": 0,
        }
        steps.append(act_step)
        await on_step(act_step)

        # Feed observation back into context
        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": observe_content})

        # Budget check
        if total_tokens >= max_tokens:
            return f"[Budget exceeded after {step_num} steps]", steps

    return f"[Max steps ({max_steps}) reached without a final answer]", steps


async def _call_llm(route: str, messages: List[Dict], session_id: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{settings.llm_gateway_url}/v1/chat/completions",
            # parent_trace_id ties this LLM call to the agent run trace
            json={"route": route, "messages": messages, "parent_trace_id": session_id},
        )
        resp.raise_for_status()
        return resp.json()


async def _call_tool(caller_id: str, tool_name: str, arguments: Dict) -> Tuple[Any, Optional[str]]:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{settings.mcp_gateway_url}/v1/tools/call",
                # trace_id ties this tool call to the agent run trace (= session_id)
                json={"tool": tool_name, "arguments": arguments, "caller_id": f"agent:{caller_id}", "trace_id": caller_id},
            )
            resp.raise_for_status()
            return resp.json().get("result"), None
    except httpx.HTTPStatusError as exc:
        return None, f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
    except Exception as exc:
        return None, str(exc)


async def _fetch_available_tools() -> List[Dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{settings.mcp_gateway_url}/v1/tools")
            resp.raise_for_status()
            return resp.json().get("tools", [])
    except Exception:
        log.warning("agent.could_not_fetch_tools")
        return []
