"""Unified trace object and abstract adapter interface.

Every LLM call through the gateway produces one LLMTrace. Provider adapters
consume that trace and ship it to their respective backend.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class LLMTrace:
    trace_id: str
    route_name: str
    provider: str
    model: str

    input_messages: List[Dict[str, str]]
    output_content: str

    latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float

    intent: Optional[str] = None
    confidence: Optional[float] = None
    hedged: bool = False
    hedge_winner: Optional[str] = None

    # When set, this LLM call is a child of a larger trace (e.g. an agent run).
    # Adapters nest the generation under this parent instead of creating a new root.
    parent_trace_id: Optional[str] = None

    # Guardrail violations detected (but not blocked)
    guardrail_warnings: List[str] = field(default_factory=list)

    metadata: Dict[str, Any] = field(default_factory=dict)


class ObservabilityAdapter(ABC):
    """Abstract base — all provider adapters implement send_trace."""

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @abstractmethod
    async def send_trace(self, trace: LLMTrace, config: Dict[str, Any]) -> None:
        """Ship the trace to the provider. Fire-and-forget — must not raise."""
        ...
