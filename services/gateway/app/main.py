import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import structlog
from fastapi import Depends, FastAPI, HTTPException, Header, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, make_asgi_app
from pydantic import BaseModel

from app.config import settings
from app.middleware.auth import require_api_key
from app.middleware.cost_tracker import get_all_stats, get_route_stats
from app.middleware.latency_tracker import get_stats as get_latency_stats
from app.router.config_store import list_routes, get_semantic_rules, poll_config, refresh_config
from app.router.llm_router import RateLimitError, RoutingError, route_request

log = structlog.get_logger()

REQUEST_COUNT = Counter("gateway_requests_total", "Total LLM requests", ["route", "status"])
REQUEST_LATENCY = Histogram("gateway_request_latency_seconds", "Request latency", ["route"])

app = FastAPI(
    title=settings.app_name,
    description="LLM Gateway — intelligent routing to Ollama with rate limiting, guardrails, cost tracking, and fallback.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    """OpenAI-compatible chat request.

    Either `model` (OpenAI standard) or `route` (legacy) names the route to
    use. `model` takes precedence so the OpenAI SDK works unmodified:

        client = OpenAI(base_url="http://localhost:8002/v1", api_key="sk-...")
        client.chat.completions.create(model="local-llama", messages=[...])
    """

    model: Optional[str] = None
    route: Optional[str] = None
    messages: List[ChatMessage]
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    # Optional parent trace id (e.g. an agent session) so this call nests
    # under one end-to-end trace instead of producing a standalone one.
    parent_trace_id: Optional[str] = None

    def resolved_route(self) -> str:
        name = self.model or self.route
        if not name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Request must include a 'model' (OpenAI) or 'route' field.",
            )
        return name


class ChatResponse(BaseModel):
    id: str
    object: str
    model: str
    choices: List[Dict[str, Any]]
    usage: Dict[str, Any]
    _route: Optional[str] = None


@app.post("/v1/chat/completions", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request, api_key: str = Depends(require_api_key)):
    route_name = req.resolved_route()
    # Prefer the authenticated key for rate-limit attribution; fall back to IP.
    client_id = api_key if api_key != "anonymous" else (request.client.host if request.client else "unknown")
    with REQUEST_LATENCY.labels(route=route_name).time():
        try:
            result = await route_request(
                route_name=route_name,
                messages=[m.model_dump() for m in req.messages],
                client_id=client_id,
                stream=req.stream,
                parent_trace_id=req.parent_trace_id,
            )
            REQUEST_COUNT.labels(route=route_name, status="success").inc()
            return JSONResponse(content=result)
        except RateLimitError as exc:
            REQUEST_COUNT.labels(route=route_name, status="rate_limited").inc()
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc))
        except RoutingError as exc:
            REQUEST_COUNT.labels(route=route_name, status="error").inc()
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@app.get("/v1/models")
async def list_models():
    """OpenAI-compatible model list. Each route is exposed as a 'model' so the
    OpenAI SDK's client.models.list() and model dropdowns work unmodified."""
    routes = list_routes()
    created = int(time.time())
    data = [
        {
            "id": r["name"] if isinstance(r, dict) else r,
            "object": "model",
            "created": created,
            "owned_by": (r.get("provider", "ollama") if isinstance(r, dict) else "ollama"),
        }
        for r in routes
    ]
    return {"object": "list", "data": data}


@app.get("/v1/routes")
async def list_available_routes():
    return {"routes": list_routes()}


@app.get("/v1/semantic-rules")
async def list_semantic_rules():
    """Shows intent → route mapping currently loaded from the control plane."""
    return {"rules": get_semantic_rules()}


@app.get("/v1/usage")
async def get_usage():
    return {"usage": get_all_stats()}


@app.get("/v1/usage/{route_name}")
async def get_route_usage(route_name: str):
    return get_route_stats(route_name)


@app.get("/v1/latency/{route_name}")
async def get_route_latency(route_name: str):
    """Returns P50/P95/P99 latency over the last 5 minutes for a route."""
    return {"route": route_name, **get_latency_stats(route_name)}


@app.post("/internal/reload")
async def reload_config():
    await refresh_config()
    return {"status": "reloaded"}


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok", "service": settings.app_name}


@app.on_event("startup")
async def startup():
    log.info("gateway.startup")
    await refresh_config()
    asyncio.create_task(poll_config())
