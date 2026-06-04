import asyncio
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Histogram, make_asgi_app

from app.api.agents import router as agents_router
from app.config import settings
from app.router.config_store import poll_config, refresh_config

log = structlog.get_logger()

AGENT_RUNS = Counter("agent_gateway_runs_total", "Total agent runs", ["status"])
AGENT_LATENCY = Histogram("agent_gateway_latency_seconds", "Agent run latency")

app = FastAPI(
    title=settings.app_name,
    description=(
        "Agent Gateway — sits between clients and AI agents. "
        "Orchestrates ReAct loops via the LLM Gateway + MCP Gateway. "
        "Enforces governance (step limits, token budgets) and traces every run to observability backends."
    ),
    version="1.0.0",
)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)
app.include_router(agents_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": settings.app_name}


@app.on_event("startup")
async def startup():
    log.info("agent_gateway.startup")
    await refresh_config()
    asyncio.create_task(poll_config())
