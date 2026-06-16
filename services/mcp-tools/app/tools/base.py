"""Lightweight tool registry shared by every toolset.

A `Tool` carries everything the MCP Gateway needs to (a) advertise the tool,
(b) route to it intelligently, and (c) extract arguments for it:
  - name / description    — human + LLM readable
  - input_schema          — JSON-schema-ish description of arguments
  - keywords              — cheap lexical hints for the gateway's fast-path router
  - tags                  — operational metadata (toolset, cacheable, rate_limit_rpm...)
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

Handler = Callable[[Dict[str, Any]], Union[Any, Awaitable[Any]]]


@dataclass
class Tool:
    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: Handler
    toolset: str = "all"
    keywords: List[str] = field(default_factory=list)
    tags: Dict[str, str] = field(default_factory=dict)

    async def invoke(self, arguments: Dict[str, Any]) -> Any:
        result = self.handler(arguments)
        if inspect.isawaitable(result):
            result = await result
        return result

    def public(self) -> Dict[str, Any]:
        """Shape advertised over the wire (no handler)."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "keywords": self.keywords,
            "tags": {"toolset": self.toolset, **self.tags},
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def add(self, t: Tool) -> None:
        if t.name in self._tools:
            raise ValueError(f"Duplicate tool registered: {t.name}")
        self._tools[t.name] = t

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def all(self, toolset: str = "all") -> List[Tool]:
        tools = list(self._tools.values())
        if toolset and toolset != "all":
            tools = [t for t in tools if t.toolset == toolset]
        return tools


REGISTRY = ToolRegistry()


def tool(
    *,
    name: str,
    description: str,
    input_schema: Dict[str, Any],
    toolset: str = "all",
    keywords: Optional[List[str]] = None,
    tags: Optional[Dict[str, str]] = None,
) -> Callable[[Handler], Handler]:
    """Decorator that registers the wrapped function as a Tool."""

    def decorator(fn: Handler) -> Handler:
        REGISTRY.add(
            Tool(
                name=name,
                description=description,
                input_schema=input_schema,
                handler=fn,
                toolset=toolset,
                keywords=keywords or [],
                tags=tags or {},
            )
        )
        return fn

    return decorator
