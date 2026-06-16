"""Async task: create or update a guardrail config."""
import logging
from typing import Any, Dict

import httpx

from app.config import settings
from app.models.guardrail import GuardrailConfig
from app.models.provision import ProvisionRequest, ProvisionStatus
from app.workers.celery_app import celery_app
from app.workers.db_session import get_sync_db

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="app.workers.tasks.configure_guardrail.configure_guardrail", max_retries=3)
def configure_guardrail(self, instance_id: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    db = get_sync_db()
    try:
        request = db.query(ProvisionRequest).filter_by(instance_id=instance_id).first()
        if not request:
            raise ValueError(f"ProvisionRequest {instance_id} not found")

        request.status = ProvisionStatus.in_progress
        db.commit()

        guardrail = _upsert_guardrail(db, parameters)
        _notify_control_plane(guardrail.name)

        request.status = ProvisionStatus.succeeded
        request.result = {"guardrail_name": guardrail.name, "guardrail_id": str(guardrail.id)}
        db.commit()

        return request.result

    except Exception as exc:
        logger.exception("Failed to configure guardrail for instance %s", instance_id)
        if request:
            request.status = ProvisionStatus.failed
            request.error_message = str(exc)
            db.commit()
        raise self.retry(exc=exc, countdown=10)
    finally:
        db.close()


def _notify_control_plane(name: str) -> None:
    """Invalidate the control plane's guardrail cache so gateways pick it up."""
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(
                f"{settings.control_plane_url}/internal/reload",
                json={"resource_type": "guardrail", "name": name},
            )
            resp.raise_for_status()
    except Exception:
        logger.warning("Could not notify control plane of guardrail %s", name)


def _upsert_guardrail(db, params: Dict[str, Any]) -> GuardrailConfig:
    existing = db.query(GuardrailConfig).filter_by(name=params["name"]).first()
    if existing:
        for k, v in params.items():
            if hasattr(existing, k):
                setattr(existing, k, v)
        db.commit()
        db.refresh(existing)
        return existing
    guardrail = GuardrailConfig(**params)
    db.add(guardrail)
    db.commit()
    db.refresh(guardrail)
    return guardrail
