from __future__ import annotations
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ObservabilityConfigCreate(BaseModel):
    name: str
    provider: str = Field(..., pattern="^(langfuse|arize|langsmith|braintrust|deepeval)$")
    project_name: str
    credentials: Dict[str, Any] = Field(
        ...,
        description=(
            "Provider-specific credentials. "
            "langfuse: {public_key, secret_key, host?}  "
            "arize: {api_key, space_key}  "
            "langsmith: {api_key}  "
            "braintrust: {api_key}  "
            "deepeval: {api_key}"
        ),
    )
    route_names: List[str] = Field(default_factory=list, description="Routes to trace. Empty = all routes.")
    endpoint_url: Optional[str] = None
    metadata_: Dict[str, Any] = Field(default_factory=dict, alias="metadata")

    model_config = {"populate_by_name": True}


class ObservabilityConfigUpdate(BaseModel):
    project_name: Optional[str] = None
    credentials: Optional[Dict[str, Any]] = None
    route_names: Optional[List[str]] = None
    endpoint_url: Optional[str] = None
    metadata_: Optional[Dict[str, Any]] = Field(None, alias="metadata")
    enabled: Optional[bool] = None

    model_config = {"populate_by_name": True}


class ObservabilityConfigOut(BaseModel):
    id: uuid.UUID
    name: str
    provider: str
    project_name: str
    remote_project_id: Optional[str]
    route_names: List[str]
    endpoint_url: Optional[str]
    enabled: bool
    provisioned: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
