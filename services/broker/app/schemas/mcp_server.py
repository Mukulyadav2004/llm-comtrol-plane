from __future__ import annotations
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, HttpUrl


class MCPServerCreate(BaseModel):
    name: str
    description: Optional[str] = None
    endpoint_url: str
    auth_header: Optional[str] = None
    capabilities: List[str] = []
    tags: Dict[str, str] = {}
    health_check_path: str = "/health"


class MCPServerUpdate(BaseModel):
    description: Optional[str] = None
    endpoint_url: Optional[str] = None
    auth_header: Optional[str] = None
    capabilities: Optional[List[str]] = None
    tags: Optional[Dict[str, str]] = None
    enabled: Optional[bool] = None


class MCPServerOut(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str]
    endpoint_url: str
    capabilities: List[str]
    tags: Dict[str, str]
    enabled: bool
    healthy: bool
    last_health_check: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
