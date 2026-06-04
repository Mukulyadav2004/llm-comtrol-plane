from __future__ import annotations
from typing import Any, Dict, List

from pydantic import BaseModel


class PlanOut(BaseModel):
    id: str
    name: str
    description: str
    metadata: Dict[str, Any] = {}


class ServiceOut(BaseModel):
    id: str
    name: str
    description: str
    plans: List[PlanOut]
    tags: List[str] = []


class CatalogOut(BaseModel):
    services: List[ServiceOut]
