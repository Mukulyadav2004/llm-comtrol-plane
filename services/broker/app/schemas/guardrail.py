from __future__ import annotations
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class GuardrailCreate(BaseModel):
    name: str
    guardrail_type: str = Field(..., pattern="^(pii|toxicity|length|regex|keyword)$")
    description: Optional[str] = None
    config: Dict[str, Any] = {}
    apply_on_input: bool = True
    apply_on_output: bool = True
    action_on_violation: str = Field("block", pattern="^(block|redact|warn)$")


class GuardrailUpdate(BaseModel):
    description: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    apply_on_input: Optional[bool] = None
    apply_on_output: Optional[bool] = None
    action_on_violation: Optional[str] = None
    enabled: Optional[bool] = None


class GuardrailOut(BaseModel):
    id: uuid.UUID
    name: str
    guardrail_type: str
    description: Optional[str]
    config: Dict[str, Any]
    apply_on_input: bool
    apply_on_output: bool
    action_on_violation: str
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
