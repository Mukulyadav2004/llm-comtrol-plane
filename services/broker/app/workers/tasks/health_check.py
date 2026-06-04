"""Periodic beat task: health-check all registered MCP servers."""
import logging
from datetime import datetime, timezone
from typing import Dict

import httpx

from app.models.mcp_server import MCPServer
from app.workers.celery_app import celery_app
from app.workers.db_session import get_sync_db

logger = logging.getLogger(__name__)


@celery_app.task(name="app.workers.tasks.health_check.check_all_mcp_servers")
def check_all_mcp_servers() -> Dict:
    db = get_sync_db()
    try:
        servers = db.query(MCPServer).filter_by(enabled=True).all()
        results = {}
        for server in servers:
            healthy = _ping(server)
            server.healthy = healthy
            server.last_health_check = datetime.now(timezone.utc)
            results[server.name] = healthy
            if not healthy:
                logger.warning("MCP server %s failed health check", server.name)
        db.commit()
        return results
    finally:
        db.close()


def _ping(server: MCPServer) -> bool:
    try:
        headers = {"Authorization": server.auth_header} if server.auth_header else {}
        with httpx.Client(timeout=4) as client:
            resp = client.get(f"{server.endpoint_url}{server.health_check_path}", headers=headers)
            return resp.status_code < 400
    except Exception:
        return False
