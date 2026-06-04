from __future__ import annotations
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.models.provision import ProvisionStatus


class ProvisionRequestCreate(BaseModel):
    service_id: str = Field(..., description="Service type: llm-route | mcp-server | guardrail")
    plan_id: str = Field(..., description="Plan within the service, e.g. ollama-basic")
    instance_id: str = Field(..., description="Unique caller-assigned ID for this instance")
    parameters: Dict[str, Any] = Field(default_factory=dict)


class ProvisionRequestOut(BaseModel):
    id: uuid.UUID
    service_id: str
    plan_id: str
    instance_id: str
    status: ProvisionStatus
    parameters: Dict[str, Any]
    result: Optional[Dict[str, Any]]
    error_message: Optional[str]
    celery_task_id: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProvisionStatusOut(BaseModel):
    instance_id: str
    status: ProvisionStatus
    result: Optional[Dict[str, Any]]
    error_message: Optional[str]

    model_config = {"from_attributes": True}
