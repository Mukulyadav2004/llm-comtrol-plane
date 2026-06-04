from celery import Celery
from app.config import settings

celery_app = Celery(
    "broker_worker",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.workers.tasks.provision_route",
        "app.workers.tasks.register_mcp",
        "app.workers.tasks.configure_guardrail",
        "app.workers.tasks.health_check",
        "app.workers.tasks.provision_observability",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "mcp-health-check-every-60s": {
            "task": "app.workers.tasks.health_check.check_all_mcp_servers",
            "schedule": 60.0,
        },
    },
)
