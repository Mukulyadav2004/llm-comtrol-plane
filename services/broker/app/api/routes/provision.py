"""Open Service Broker-style provision/deprovision/status endpoints."""
import uuid
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.models.provision import ProvisionRequest, ProvisionStatus
from app.schemas.provision import ProvisionRequestCreate, ProvisionRequestOut, ProvisionStatusOut
from app.workers.tasks.provision_route import provision_llm_route
from app.workers.tasks.register_mcp import register_mcp_server
from app.workers.tasks.configure_guardrail import configure_guardrail

router = APIRouter(prefix="/v2/service_instances", tags=["provision"])

_TASK_MAP: Dict[str, Any] = {
    "llm-route": provision_llm_route,
    "mcp-server": register_mcp_server,
    "guardrail": configure_guardrail,
}


@router.put("/{instance_id}", response_model=ProvisionRequestOut, status_code=status.HTTP_202_ACCEPTED)
async def provision(instance_id: str, body: ProvisionRequestCreate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ProvisionRequest).where(ProvisionRequest.instance_id == instance_id))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Instance already exists")

    task_fn = _TASK_MAP.get(body.service_id)
    if not task_fn:
        raise HTTPException(status_code=400, detail=f"Unknown service_id: {body.service_id}")

    record = ProvisionRequest(
        service_id=body.service_id,
        plan_id=body.plan_id,
        instance_id=instance_id,
        parameters=body.parameters,
        status=ProvisionStatus.pending,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    task = task_fn.delay(instance_id, body.parameters)
    record.celery_task_id = task.id
    await db.commit()
    await db.refresh(record)

    return record


@router.get("/{instance_id}/last_operation", response_model=ProvisionStatusOut)
async def get_status(instance_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ProvisionRequest).where(ProvisionRequest.instance_id == instance_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Instance not found")
    return record


@router.delete("/{instance_id}", status_code=status.HTTP_200_OK)
async def deprovision(instance_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ProvisionRequest).where(ProvisionRequest.instance_id == instance_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=410, detail="Instance does not exist")
    await db.delete(record)
    await db.commit()
    return {"message": f"Instance {instance_id} deprovisioned"}
