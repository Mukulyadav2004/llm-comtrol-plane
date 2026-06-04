"""CRUD for semantic routing rules — maps detected intents to LLM routes."""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.models.semantic_route import SemanticRoutingRule
from app.schemas.semantic_route import (
    SemanticRoutingRuleCreate,
    SemanticRoutingRuleOut,
    SemanticRoutingRuleUpdate,
)

router = APIRouter(prefix="/v2/semantic-routes", tags=["semantic-routing"])


@router.get("", response_model=List[SemanticRoutingRuleOut])
async def list_rules(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SemanticRoutingRule).order_by(SemanticRoutingRule.priority.desc())
    )
    return result.scalars().all()


@router.post("", response_model=SemanticRoutingRuleOut, status_code=status.HTTP_201_CREATED)
async def create_rule(body: SemanticRoutingRuleCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(
        select(SemanticRoutingRule).where(SemanticRoutingRule.intent == body.intent)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Rule for intent '{body.intent}' already exists")
    rule = SemanticRoutingRule(**body.model_dump())
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.patch("/{intent}", response_model=SemanticRoutingRuleOut)
async def update_rule(intent: str, body: SemanticRoutingRuleUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SemanticRoutingRule).where(SemanticRoutingRule.intent == intent)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(rule, field, value)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/{intent}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(intent: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SemanticRoutingRule).where(SemanticRoutingRule.intent == intent)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    await db.delete(rule)
    await db.commit()
