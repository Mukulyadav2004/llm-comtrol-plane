from __future__ import annotations
import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class SemanticRoutingRuleCreate(BaseModel):
    intent: str
    route_name: str
    description: Optional[str] = None
    keyword_hints: List[str] = []
    priority: int = 0


class SemanticRoutingRuleUpdate(BaseModel):
    route_name: Optional[str] = None
    description: Optional[str] = None
    keyword_hints: Optional[List[str]] = None
    priority: Optional[int] = None
    enabled: Optional[bool] = None


class SemanticRoutingRuleOut(BaseModel):
    id: uuid.UUID
    intent: str
    route_name: str
    description: Optional[str]
    keyword_hints: List[str]
    priority: int
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
