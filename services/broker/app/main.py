import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app

from app.api.routes.catalog import router as catalog_router
from app.api.routes.provision import router as provision_router
from app.api.routes.routes_api import router as routes_router
from app.api.routes.mcp_api import router as mcp_router
from app.api.routes.guardrail_api import router as guardrail_router
from app.api.routes.semantic_route_api import router as semantic_router
from app.api.routes.observability_api import router as observability_router
from app.config import settings

log = structlog.get_logger()

app = FastAPI(
    title=settings.app_name,
    description="Open Service Broker for AI Gateway resources (LLM routes, MCP servers, guardrails)",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

app.include_router(catalog_router)
app.include_router(provision_router)
app.include_router(routes_router)
app.include_router(mcp_router)
app.include_router(guardrail_router)
app.include_router(semantic_router)
app.include_router(observability_router)


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok", "service": settings.app_name}


@app.on_event("startup")
async def startup():
    log.info("broker.startup", service=settings.app_name)
