"""CRUD for observability configs + async provisioning trigger."""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.models.observability_config import ObservabilityConfig
from app.models.provision import ProvisionRequest, ProvisionStatus
from app.schemas.observability_config import (
    ObservabilityConfigCreate,
    ObservabilityConfigOut,
    ObservabilityConfigUpdate,
)
from app.workers.tasks.provision_observability import provision_observability

router = APIRouter(prefix="/v2/observability", tags=["observability"])


@router.get("", response_model=List[ObservabilityConfigOut])
async def list_configs(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ObservabilityConfig).order_by(ObservabilityConfig.created_at.desc()))
    return result.scalars().all()


@router.post("", response_model=ObservabilityConfigOut, status_code=status.HTTP_202_ACCEPTED)
async def create_config(body: ObservabilityConfigCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(ObservabilityConfig).where(ObservabilityConfig.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Config '{body.name}' already exists")

    config = ObservabilityConfig(
        name=body.name,
        provider=body.provider,
        project_name=body.project_name,
        credentials=body.credentials,
        route_names=body.route_names,
        endpoint_url=body.endpoint_url,
        metadata_=body.metadata_,
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)

    # Kick off async provisioning task (validate keys + create remote project)
    instance_id = f"obs-{config.name}"
    record = ProvisionRequest(
        service_id="observability",
        plan_id=body.provider,
        instance_id=instance_id,
        parameters={"config_name": config.name},
        status=ProvisionStatus.pending,
    )
    db.add(record)
    await db.commit()

    task = provision_observability.delay(instance_id, {"config_name": config.name})
    record.celery_task_id = task.id
    await db.commit()

    return config


@router.get("/{name}", response_model=ObservabilityConfigOut)
async def get_config(name: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ObservabilityConfig).where(ObservabilityConfig.name == name))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")
    return config


@router.patch("/{name}", response_model=ObservabilityConfigOut)
async def update_config(name: str, body: ObservabilityConfigUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ObservabilityConfig).where(ObservabilityConfig.name == name))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")
    for field, value in body.model_dump(exclude_none=True, by_alias=False).items():
        setattr(config, field, value)
    await db.commit()
    await db.refresh(config)
    return config


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_config(name: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ObservabilityConfig).where(ObservabilityConfig.name == name))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")
    await db.delete(config)
    await db.commit()
