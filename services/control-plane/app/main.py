import asyncio
import structlog
from fastapi import FastAPI
from prometheus_client import make_asgi_app

from app.api.config import router as config_router
from app.config import settings
from app.context.loader import build_full_context

log = structlog.get_logger()

app = FastAPI(
    title=settings.app_name,
    description="Dynamic config distribution for the LLM Gateway. Renders Jinja2 templates with live broker context.",
    version="1.0.0",
)

metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

app.include_router(config_router)


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok", "service": settings.app_name}


async def _context_refresh_loop():
    """Background loop: periodically invalidates Redis cache so config stays fresh."""
    while True:
        await asyncio.sleep(settings.context_refresh_interval)
        try:
            build_full_context()
            log.info("control_plane.context_refreshed")
        except Exception:
            log.exception("control_plane.context_refresh_failed")


@app.on_event("startup")
async def startup():
    log.info("control_plane.startup", service=settings.app_name)
    asyncio.create_task(_context_refresh_loop())
