"""Async task: provision an LLM route and notify the control plane."""
import logging
from typing import Any, Dict

import httpx
from sqlalchemy.orm import Session
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.models.provision import ProvisionRequest, ProvisionStatus
from app.models.route import LLMRoute
from app.workers.celery_app import celery_app
from app.workers.db_session import get_sync_db

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="app.workers.tasks.provision_route.provision_llm_route", max_retries=3)
def provision_llm_route(self, instance_id: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    db: Session = get_sync_db()
    try:
        request = db.query(ProvisionRequest).filter_by(instance_id=instance_id).first()
        if not request:
            raise ValueError(f"ProvisionRequest {instance_id} not found")

        request.status = ProvisionStatus.in_progress
        db.commit()

        route = _create_or_update_route(db, parameters)
        _notify_control_plane(route)

        request.status = ProvisionStatus.succeeded
        request.result = {"route_name": route.name, "route_id": str(route.id)}
        db.commit()

        return request.result

    except Exception as exc:
        logger.exception("Failed to provision route for instance %s", instance_id)
        if request:
            request.status = ProvisionStatus.failed
            request.error_message = str(exc)
            db.commit()
        raise self.retry(exc=exc, countdown=2 ** self.request.retries * 10)
    finally:
        db.close()


def _create_or_update_route(db: Session, params: Dict[str, Any]) -> LLMRoute:
    existing = db.query(LLMRoute).filter_by(name=params["name"]).first()
    if existing:
        for key, value in params.items():
            if hasattr(existing, key):
                setattr(existing, key, value)
        db.commit()
        db.refresh(existing)
        return existing

    route = LLMRoute(**params)
    db.add(route)
    db.commit()
    db.refresh(route)
    return route


@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=1, max=10))
def _notify_control_plane(route: LLMRoute) -> None:
    """Push updated route config to the control plane so the gateway gets it."""
    with httpx.Client(timeout=10) as client:
        resp = client.post(
            f"{settings.control_plane_url}/internal/reload",
            json={"resource_type": "route", "name": route.name},
        )
        resp.raise_for_status()
