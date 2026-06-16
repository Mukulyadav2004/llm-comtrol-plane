"""Knowledge tools that reach out to public APIs.

Both tools degrade gracefully: if the network call fails (offline demo, rate
limit, timeout) they return a structured "unavailable" payload instead of
raising, so the gateway/agent flow can still be demonstrated end-to-end.
"""
from __future__ import annotations

from typing import Any, Dict
from urllib.parse import quote

import httpx

from app.tools.base import tool


@tool(
    name="web_search",
    description="Search the web for a query and return a short instant-answer summary plus related topics.",
    input_schema={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Natural-language search query."}},
        "required": ["query"],
    },
    toolset="knowledge",
    keywords=["search", "web", "google", "find", "look up", "latest", "news", "internet"],
    tags={"cacheable": "true", "cache_ttl": "120"},
)
async def web_search(args: Dict[str, Any]) -> Dict[str, Any]:
    query = str(args.get("query", "")).strip()
    if not query:
        raise ValueError("'query' is required")
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            )
            resp.raise_for_status()
            data = resp.json()

        related = [
            t.get("Text")
            for t in data.get("RelatedTopics", [])
            if isinstance(t, dict) and t.get("Text")
        ][:5]
        return {
            "query": query,
            "summary": data.get("AbstractText") or data.get("Heading") or "No instant answer found.",
            "source": data.get("AbstractURL") or "",
            "related": related,
        }
    except Exception as exc:
        return {"query": query, "summary": None, "available": False, "error": str(exc)}


@tool(
    name="wikipedia",
    description="Look up a topic on Wikipedia and return the summary extract for the matching page.",
    input_schema={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Topic or article title, e.g. 'Alan Turing'."}},
        "required": ["query"],
    },
    toolset="knowledge",
    keywords=["wikipedia", "who is", "what is", "define", "encyclopedia", "biography", "explain"],
    tags={"cacheable": "true", "cache_ttl": "3600"},
)
async def wikipedia(args: Dict[str, Any]) -> Dict[str, Any]:
    query = str(args.get("query", "")).strip()
    if not query:
        raise ValueError("'query' is required")
    title = quote(query.replace(" ", "_"))
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            resp = await client.get(
                f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}",
                headers={"User-Agent": "llm-control-plane-mcp-tools/1.0"},
            )
            if resp.status_code == 404:
                return {"query": query, "summary": None, "available": False, "error": "No matching article"}
            resp.raise_for_status()
            data = resp.json()
        return {
            "query": query,
            "title": data.get("title"),
            "summary": data.get("extract"),
            "source": (data.get("content_urls", {}).get("desktop", {}) or {}).get("page", ""),
        }
    except Exception as exc:
        return {"query": query, "summary": None, "available": False, "error": str(exc)}
