"""Intelligent tool routing for the MCP Gateway.

Given a natural-language request, pick the best tool from the discovered catalog
— mirroring the LLM Gateway's two-stage semantic router:

  Stage 1 — lexical scan (O(n)): score every tool against the query using its
            name, keywords and description. If the top score is confident enough
            we skip the LLM entirely (fast + free + deterministic).
  Stage 2 — LLM selection: ask the LLM Gateway to choose the best tool from the
            catalog. Falls back to the best lexical candidate on any failure.

`extract_arguments` then uses the LLM (with the tool's input_schema) to turn the
request into a concrete argument object, so the gateway can execute end-to-end.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import settings

log = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> List[str]:
    return _WORD_RE.findall(text.lower())


def lexical_scores(query: str, catalog: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], float]]:
    """Score each tool 0..1 by lexical overlap with the query. Highest first."""
    q = set(_tokens(query))
    if not q:
        return [(t, 0.0) for t in catalog]

    scored: List[Tuple[Dict[str, Any], float]] = []
    for tool in catalog:
        name_toks = set(_tokens(tool["tool"]))
        kw_toks = set(t for kw in tool.get("keywords", []) for t in _tokens(kw))
        desc_toks = set(_tokens(tool.get("description", "")))

        # Weighted overlap: keywords are the strongest signal, then name, then desc.
        score = 0.0
        score += 3.0 * len(q & name_toks)
        score += 2.0 * len(q & kw_toks)
        score += 1.0 * len(q & desc_toks)
        # Normalise by query length so short queries can still reach high confidence.
        normaliser = 2.0 * len(q)
        scored.append((tool, min(1.0, score / normaliser) if normaliser else 0.0))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


async def select_tool(query: str, catalog: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return {tool, confidence, method, reason, candidates}. tool may be None."""
    if not catalog:
        return {"tool": None, "confidence": 0.0, "method": "none", "reason": "no tools registered", "candidates": []}

    scored = lexical_scores(query, catalog)
    candidates = [{"tool": t["tool"], "score": round(s, 3)} for t, s in scored[:5]]
    best_tool, best_score = scored[0]

    # Stage 1 — confident lexical match → no LLM needed.
    if best_score >= settings.routing_keyword_threshold:
        log.info("tool_router.keyword_match", tool=best_tool["tool"], score=best_score)
        return {
            "tool": best_tool["tool"], "confidence": round(best_score, 3),
            "method": "keyword", "reason": "strong lexical match", "candidates": candidates,
        }

    # Stage 2 — ask the LLM to choose from the catalog.
    if settings.routing_llm_enabled:
        valid_names = {t["tool"] for t in catalog}
        llm_choice = await _llm_select(query, catalog)
        if llm_choice and llm_choice.get("tool") in valid_names:
            log.info("tool_router.llm_match", tool=llm_choice["tool"], confidence=llm_choice.get("confidence"))
            return {
                "tool": llm_choice["tool"], "confidence": llm_choice.get("confidence", 0.5),
                "method": "llm", "reason": llm_choice.get("reason", "selected by LLM"), "candidates": candidates,
            }

    # Fallback — best lexical candidate (even if weak).
    log.info("tool_router.fallback", tool=best_tool["tool"], score=best_score)
    return {
        "tool": best_tool["tool"], "confidence": round(best_score, 3),
        "method": "fallback", "reason": "best available lexical match", "candidates": candidates,
    }


async def extract_arguments(query: str, tool_meta: Dict[str, Any]) -> Dict[str, Any]:
    """Turn the natural-language request into arguments matching the tool's schema."""
    schema = tool_meta.get("input_schema") or {}
    if not settings.routing_llm_enabled or not schema.get("properties"):
        return _heuristic_arguments(query, schema)

    prompt = (
        "You convert a user request into JSON arguments for a tool.\n"
        f"Tool: {tool_meta['tool']} — {tool_meta.get('description', '')}\n"
        f"Argument JSON schema: {json.dumps(schema)}\n\n"
        f"User request: {query}\n\n"
        "Output ONLY a JSON object with the argument values. No markdown, no prose."
    )
    try:
        content = await _chat(prompt)
        args = _parse_json(content)
        if isinstance(args, dict):
            return args
    except Exception:
        log.warning("tool_router.arg_extraction_failed", tool=tool_meta.get("tool"))
    return _heuristic_arguments(query, schema)


def _heuristic_arguments(query: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    """No-LLM fallback: drop the raw query into the most likely string property."""
    props = (schema or {}).get("properties", {})
    for key in ("query", "expression", "text"):
        if key in props:
            return {key: query}
    # Otherwise put it in the first required (or first) string property.
    required = (schema or {}).get("required", [])
    for key in list(required) + list(props.keys()):
        if props.get(key, {}).get("type") == "string":
            return {key: query}
    return {}


async def _llm_select(query: str, catalog: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    listing = "\n".join(f"- {t['tool']}: {t.get('description', '')}" for t in catalog)
    prompt = (
        "You are a tool router. Choose the single best tool for the user request.\n"
        "Available tools:\n"
        f"{listing}\n\n"
        f"User request: {query}\n\n"
        'Output ONLY JSON: {"tool": "<name>", "confidence": <0..1>, "reason": "<short>"}'
    )
    try:
        content = await _chat(prompt)
        parsed = _parse_json(content)
        if isinstance(parsed, dict) and parsed.get("tool"):
            return parsed
    except Exception:
        log.warning("tool_router.llm_select_failed")
    return None


async def _chat(prompt: str) -> str:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{settings.llm_gateway_url}/v1/chat/completions",
            json={"route": settings.routing_route, "messages": [{"role": "user", "content": prompt}]},
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def _parse_json(content: str) -> Any:
    content = content.strip()
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    # Be tolerant of leading/trailing prose around the JSON object.
    start, end = content.find("{"), content.rfind("}")
    if start != -1 and end != -1:
        content = content[start : end + 1]
    return json.loads(content)
