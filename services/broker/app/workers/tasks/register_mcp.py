"""Async task: register an MCP server and verify reachability."""
import logging
from typing import Any, Dict

import httpx

from app.models.mcp_server import MCPServer
from app.models.provision import ProvisionRequest, ProvisionStatus
from app.workers.celery_app import celery_app
from app.workers.db_session import get_sync_db

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="app.workers.tasks.register_mcp.register_mcp_server", max_retries=3)
def register_mcp_server(self, instance_id: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    db = get_sync_db()
    try:
        request = db.query(ProvisionRequest).filter_by(instance_id=instance_id).first()
        if not request:
            raise ValueError(f"ProvisionRequest {instance_id} not found")

        request.status = ProvisionStatus.in_progress
        db.commit()

        server = _upsert_mcp_server(db, parameters)
        healthy = _health_check_server(server)
        server.healthy = healthy
        db.commit()

        request.status = ProvisionStatus.succeeded
        request.result = {"server_name": server.name, "server_id": str(server.id), "healthy": healthy}
        db.commit()

        return request.result

    except Exception as exc:
        logger.exception("Failed to register MCP server for instance %s", instance_id)
        if request:
            request.status = ProvisionStatus.failed
            request.error_message = str(exc)
            db.commit()
        raise self.retry(exc=exc, countdown=15)
    finally:
        db.close()


def _upsert_mcp_server(db, params: Dict[str, Any]) -> MCPServer:
    existing = db.query(MCPServer).filter_by(name=params["name"]).first()
    if existing:
        for k, v in params.items():
            if hasattr(existing, k):
                setattr(existing, k, v)
        db.commit()
        db.refresh(existing)
        return existing
    server = MCPServer(**params)
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


def _health_check_server(server: MCPServer) -> bool:
    try:
        headers = {}
        if server.auth_header:
            headers["Authorization"] = server.auth_header
        with httpx.Client(timeout=5) as client:
            resp = client.get(f"{server.endpoint_url}{server.health_check_path}", headers=headers)
            return resp.status_code < 400
    except Exception:
        return False
