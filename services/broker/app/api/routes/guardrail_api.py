from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.models.guardrail import GuardrailConfig
from app.schemas.guardrail import GuardrailOut, GuardrailUpdate

router = APIRouter(prefix="/v2/guardrails", tags=["guardrails"])


@router.get("", response_model=List[GuardrailOut])
async def list_guardrails(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(GuardrailConfig).order_by(GuardrailConfig.name))
    return result.scalars().all()


@router.get("/{name}", response_model=GuardrailOut)
async def get_guardrail(name: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(GuardrailConfig).where(GuardrailConfig.name == name))
    g = result.scalar_one_or_none()
    if not g:
        raise HTTPException(status_code=404, detail="Guardrail not found")
    return g


@router.patch("/{name}", response_model=GuardrailOut)
async def update_guardrail(name: str, body: GuardrailUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(GuardrailConfig).where(GuardrailConfig.name == name))
    g = result.scalar_one_or_none()
    if not g:
        raise HTTPException(status_code=404, detail="Guardrail not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(g, field, value)
    await db.commit()
    await db.refresh(g)
    return g
