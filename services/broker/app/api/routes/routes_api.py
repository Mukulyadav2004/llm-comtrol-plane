"""CRUD for LLM routes — read/update without full provisioning flow."""
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.models.route import LLMRoute
from app.schemas.route import LLMRouteOut, LLMRouteUpdate

router = APIRouter(prefix="/v2/routes", tags=["routes"])


@router.get("", response_model=List[LLMRouteOut])
async def list_routes(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(LLMRoute).order_by(LLMRoute.created_at.desc()))
    return result.scalars().all()


@router.get("/{name}", response_model=LLMRouteOut)
async def get_route(name: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(LLMRoute).where(LLMRoute.name == name))
    route = result.scalar_one_or_none()
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    return route


@router.patch("/{name}", response_model=LLMRouteOut)
async def update_route(name: str, body: LLMRouteUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(LLMRoute).where(LLMRoute.name == name))
    route = result.scalar_one_or_none()
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(route, field, value)
    await db.commit()
    await db.refresh(route)
    return route


@router.delete("/{name}", status_code=204)
async def delete_route(name: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(LLMRoute).where(LLMRoute.name == name))
    route = result.scalar_one_or_none()
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    await db.delete(route)
    await db.commit()
