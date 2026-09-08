from __future__ import annotations
import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

# Kept in step with the gateway's provider registry
# (services/gateway/app/providers/__init__.py). Accepting a name the data
# plane cannot serve is how you provision a route that 502s at request time.
SUPPORTED_PROVIDERS = ("ollama", "openai_compatible", "anthropic")
PROVIDER_PATTERN = "^(" + "|".join(SUPPORTED_PROVIDERS) + ")$"


class LLMRouteCreate(BaseModel):
    name: str
    provider: str = Field(..., pattern=PROVIDER_PATTERN)
    model: str
    base_url: Optional[str] = None
    # Name of the env var holding the key, e.g. "GROQ_API_KEY". Never the key.
    api_key_env: Optional[str] = None
    provider_options: dict = Field(default_factory=dict)
    max_tokens: int = 4096
    temperature: float = 0.7
    rate_limit_rpm: int = 60
    cost_per_1k_tokens: float = 0.0
    fallback_route_name: Optional[str] = None
    guardrail_ids: List[str] = Field(default_factory=list)


class LLMRouteUpdate(BaseModel):
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key_env: Optional[str] = None
    provider_options: Optional[dict] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    rate_limit_rpm: Optional[int] = None
    fallback_route_name: Optional[str] = None
    guardrail_ids: Optional[List[str]] = None
    enabled: Optional[bool] = None


class LLMRouteOut(BaseModel):
    id: uuid.UUID
    name: str
    provider: str
    model: str
    base_url: Optional[str]
    api_key_env: Optional[str] = None
    provider_options: dict = Field(default_factory=dict)
    max_tokens: int
    temperature: float
    rate_limit_rpm: int
    cost_per_1k_tokens: float
    fallback_route_name: Optional[str]
    guardrail_ids: List[str]
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
