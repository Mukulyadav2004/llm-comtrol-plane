"""Provisioning task: validate credentials and create the remote project
in the target observability platform.

This is the AI-domain equivalent of the Route53 / CloudFront provisioning
tasks in the original Atlassian architecture. Each provider sub-function:
  1. Calls the provider's REST API to verify the API key is valid.
  2. Creates (or fetches) the project and returns its remote ID.
  3. Writes the result back to ObservabilityConfig.remote_project_id.

Providers: langfuse | arize | langsmith | braintrust | deepeval
"""
import logging
from typing import Any, Dict

import httpx

from app.models.observability_config import ObservabilityConfig
from app.models.provision import ProvisionRequest, ProvisionStatus
from app.workers.celery_app import celery_app
from app.workers.db_session import get_sync_db

log = logging.getLogger(__name__)


@celery_app.task(bind=True, name="app.workers.tasks.provision_observability.provision_observability", max_retries=3)
def provision_observability(self, instance_id: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    db = get_sync_db()
    try:
        request = db.query(ProvisionRequest).filter_by(instance_id=instance_id).first()
        if not request:
            raise ValueError(f"ProvisionRequest {instance_id} not found")

        request.status = ProvisionStatus.in_progress
        db.commit()

        config_name = parameters["config_name"]
        config = db.query(ObservabilityConfig).filter_by(name=config_name).first()
        if not config:
            raise ValueError(f"ObservabilityConfig '{config_name}' not found")

        remote_id = _provision_for_provider(config.provider, config.project_name, config.credentials, config.endpoint_url)

        config.remote_project_id = remote_id
        config.provisioned = True
        db.commit()

        request.status = ProvisionStatus.succeeded
        request.result = {"provider": config.provider, "project_name": config.project_name, "remote_project_id": remote_id}
        db.commit()

        log.info("observability.provisioned", provider=config.provider, project=config.project_name, remote_id=remote_id)
        return request.result

    except Exception as exc:
        log.exception("observability.provision_failed", instance_id=instance_id)
        if request:
            request.status = ProvisionStatus.failed
            request.error_message = str(exc)
            db.commit()
        raise self.retry(exc=exc, countdown=15)
    finally:
        db.close()


# ─── Provider provisioning functions ─────────────────────────────────────────

def _provision_for_provider(provider: str, project_name: str, creds: Dict, endpoint_url: str) -> str:
    fn = {
        "langfuse": _provision_langfuse,
        "arize": _provision_arize,
        "langsmith": _provision_langsmith,
        "braintrust": _provision_braintrust,
        "deepeval": _provision_deepeval,
    }.get(provider)
    if not fn:
        raise ValueError(f"Unknown provider: {provider}")
    return fn(project_name, creds, endpoint_url)


def _provision_langfuse(project_name: str, creds: Dict, endpoint_url: str) -> str:
    """Validate Langfuse keys and fetch/create the project via their REST API."""
    host = (endpoint_url or creds.get("host") or "https://cloud.langfuse.com").rstrip("/")
    public_key = creds["public_key"]
    secret_key = creds["secret_key"]

    with httpx.Client(timeout=10, auth=(public_key, secret_key)) as client:
        # Validate credentials — /api/public/projects returns all projects
        resp = client.get(f"{host}/api/public/projects")
        if resp.status_code == 401:
            raise PermissionError("Langfuse: invalid public_key / secret_key")
        resp.raise_for_status()

        projects = resp.json().get("data", [])
        for p in projects:
            if p["name"] == project_name:
                log.info("langfuse.project_exists", project=project_name, id=p["id"])
                return p["id"]

        # Create project
        create_resp = client.post(f"{host}/api/public/projects", json={"name": project_name})
        create_resp.raise_for_status()
        project_id = create_resp.json()["id"]
        log.info("langfuse.project_created", project=project_name, id=project_id)
        return project_id


def _provision_arize(project_name: str, creds: Dict, endpoint_url: str) -> str:
    """Validate Arize credentials by querying their GraphQL API."""
    api_key = creds["api_key"]
    space_key = creds["space_key"]

    query = """
    query { spaces { id name } }
    """
    with httpx.Client(timeout=10) as client:
        resp = client.post(
            "https://api.arize.com/graphql",
            json={"query": query},
            headers={"x-api-key": api_key, "space": space_key},
        )
        if resp.status_code in (401, 403):
            raise PermissionError("Arize: invalid api_key or space_key")
        resp.raise_for_status()

        spaces = resp.json().get("data", {}).get("spaces", [])
        for s in spaces:
            if s["name"] == project_name:
                return s["id"]

        # Arize uses spaces pre-created in dashboard; return the space_key as ID
        log.info("arize.using_space_key", space_key=space_key)
        return space_key


def _provision_langsmith(project_name: str, creds: Dict, endpoint_url: str) -> str:
    """Validate LangSmith API key and create a tracing project."""
    api_key = creds["api_key"]
    host = (endpoint_url or "https://api.smith.langchain.com").rstrip("/")

    with httpx.Client(timeout=10, headers={"x-api-key": api_key}) as client:
        # Check auth
        me_resp = client.get(f"{host}/api/v1/me")
        if me_resp.status_code == 401:
            raise PermissionError("LangSmith: invalid api_key")
        me_resp.raise_for_status()

        # List existing projects
        list_resp = client.get(f"{host}/api/v1/projects")
        list_resp.raise_for_status()
        projects = list_resp.json()
        for p in projects:
            if p.get("name") == project_name:
                log.info("langsmith.project_exists", project=project_name, id=p["id"])
                return p["id"]

        # Create project
        create_resp = client.post(f"{host}/api/v1/projects", json={"name": project_name})
        create_resp.raise_for_status()
        project_id = create_resp.json()["id"]
        log.info("langsmith.project_created", project=project_name, id=project_id)
        return project_id


def _provision_braintrust(project_name: str, creds: Dict, endpoint_url: str) -> str:
    """Validate Braintrust API key and create a project via their REST API."""
    api_key = creds["api_key"]
    host = (endpoint_url or "https://api.braintrust.dev").rstrip("/")

    with httpx.Client(timeout=10, headers={"Authorization": f"Bearer {api_key}"}) as client:
        # Validate
        me_resp = client.get(f"{host}/v1/me")
        if me_resp.status_code == 401:
            raise PermissionError("Braintrust: invalid api_key")
        me_resp.raise_for_status()

        # List projects
        list_resp = client.get(f"{host}/v1/project")
        list_resp.raise_for_status()
        for p in list_resp.json().get("objects", []):
            if p["name"] == project_name:
                log.info("braintrust.project_exists", project=project_name, id=p["id"])
                return p["id"]

        create_resp = client.post(f"{host}/v1/project", json={"name": project_name})
        create_resp.raise_for_status()
        project_id = create_resp.json()["id"]
        log.info("braintrust.project_created", project=project_name, id=project_id)
        return project_id


def _provision_deepeval(project_name: str, creds: Dict, endpoint_url: str) -> str:
    """Validate DeepEval / Confident AI key and create a project."""
    api_key = creds["api_key"]
    host = (endpoint_url or "https://api.confident-ai.com").rstrip("/")

    with httpx.Client(timeout=10, headers={"Authorization": f"Bearer {api_key}"}) as client:
        auth_resp = client.get(f"{host}/api/v1/auth/verify")
        if auth_resp.status_code == 401:
            raise PermissionError("DeepEval: invalid api_key")
        auth_resp.raise_for_status()

        list_resp = client.get(f"{host}/api/v1/projects")
        if list_resp.status_code == 200:
            for p in list_resp.json().get("projects", []):
                if p.get("name") == project_name:
                    return p["id"]

        create_resp = client.post(f"{host}/api/v1/projects", json={"name": project_name})
        create_resp.raise_for_status()
        project_id = create_resp.json().get("id", project_name)
        log.info("deepeval.project_created", project=project_name, id=project_id)
        return project_id
