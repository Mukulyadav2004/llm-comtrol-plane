import asyncio
import structlog
from fastapi import FastAPI
from prometheus_client import make_asgi_app

from app.api.tools import router as tools_router
from app.config import settings
from app.proxy.mcp_proxy import refresh_servers

log = structlog.get_logger()

app = FastAPI(
    title=settings.app_name,
    description="MCP Tool Registry — discovers and proxies calls to registered MCP tool servers.",
    version="1.0.0",
)

metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

app.include_router(tools_router)


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok", "service": settings.app_name}


async def _periodic_refresh():
    while True:
        await asyncio.sleep(60)
        await refresh_servers()


@app.on_event("startup")
async def startup():
    log.info("mcp_registry.startup")
    await refresh_servers()
    asyncio.create_task(_periodic_refresh())
