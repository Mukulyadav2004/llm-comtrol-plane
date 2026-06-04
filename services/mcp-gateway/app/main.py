import asyncio
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Histogram, make_asgi_app

from app.api.tools import router as tools_router
from app.config import settings
from app.router.config_store import poll_config, refresh_config

log = structlog.get_logger()

TOOL_CALLS = Counter("mcp_gateway_tool_calls_total", "Total tool calls", ["tool", "status"])
TOOL_LATENCY = Histogram("mcp_gateway_tool_latency_seconds", "Tool call latency", ["tool"])

app = FastAPI(
    title=settings.app_name,
    description=(
        "MCP Gateway — sits between agents/clients and MCP tool servers. "
        "Provides authentication, per-tool rate limiting, and full observability of every tool call."
    ),
    version="1.0.0",
)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)
app.include_router(tools_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": settings.app_name}


@app.on_event("startup")
async def startup():
    log.info("mcp_gateway.startup")
    await refresh_config()
    asyncio.create_task(poll_config())
