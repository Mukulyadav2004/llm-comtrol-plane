"""Dashboard backend — serves the UI and proxies to control-plane services."""
import asyncio
import binascii
import logging
import os
import secrets
from base64 import b64decode
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

BROKER = os.getenv("BROKER_URL", "http://broker:8000")
GATEWAY = os.getenv("GATEWAY_URL", "http://gateway:8002")
CONTROL_PLANE = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8001")
MCP_GATEWAY = os.getenv("MCP_GATEWAY_URL", "http://mcp-gateway:8005")
AGENT_GATEWAY = os.getenv("AGENT_GATEWAY_URL", "http://agent-gateway:8004")
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "demo")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
HOSTED_DEMO_SEED = os.getenv("HOSTED_DEMO_SEED", "") == "1"

log = logging.getLogger(__name__)

app = FastAPI(docs_url=None, redoc_url=None)

_HTML = (Path(__file__).parent / "static" / "index.html").read_text()


@app.on_event("startup")
async def seed_hosted_demo_on_startup():
    if HOSTED_DEMO_SEED:
        app.state.demo_seed_task = asyncio.create_task(_seed_hosted_demo())


async def _seed_hosted_demo():
    """Provision a small, idempotent cloud demo after Broker comes online."""
    instances = {
        "hosted-groq-route": {
            "service_id": "llm-route", "plan_id": "standard", "instance_id": "hosted-groq-route",
            "parameters": {
                "name": "groq-demo", "provider": "openai_compatible",
                "model": "openai/gpt-oss-20b", "base_url": "https://api.groq.com/openai/v1",
                "api_key_env": "GROQ_API_KEY", "rate_limit_rpm": 5,
                "max_tokens": 256, "cost_per_1k_tokens": 0.0003,
            },
        },
        "hosted-utility-tools": {
            "service_id": "mcp-server", "plan_id": "standard", "instance_id": "hosted-utility-tools",
            "parameters": {
                "name": "utility-tools", "endpoint_url": os.getenv(
                    "MCP_UTILITY_URL", "http://mcp-tools-utility.railway.internal:8100"),
                "description": "Math, time, unit and text utilities",
                "capabilities": ["calculator", "current_datetime", "unit_convert", "random_number", "text_stats"],
                "tags": {"category": "utility"}, "health_check_path": "/health",
            },
        },
        "hosted-knowledge-tools": {
            "service_id": "mcp-server", "plan_id": "standard", "instance_id": "hosted-knowledge-tools",
            "parameters": {
                "name": "knowledge-tools", "endpoint_url": os.getenv(
                    "MCP_KNOWLEDGE_URL", "http://mcp-tools-knowledge.railway.internal:8100"),
                "description": "Web search and Wikipedia lookup",
                "capabilities": ["web_search", "wikipedia"],
                "tags": {"category": "knowledge"}, "health_check_path": "/health",
            },
        },
    }
    async with httpx.AsyncClient(timeout=5) as client:
        for attempt in range(30):
            try:
                for instance_id, body in instances.items():
                    response = await client.put(f"{BROKER}/v2/service_instances/{instance_id}", json=body)
                    if response.status_code not in (202, 409):
                        response.raise_for_status()
                log.info("Hosted demo provisioning queued")
                return
            except (httpx.HTTPError, httpx.RequestError):
                log.warning("Hosted demo provisioning attempt %s failed", attempt + 1)
                await asyncio.sleep(2)
    log.error("Hosted demo provisioning did not complete")


@app.middleware("http")
async def dashboard_auth(request: Request, call_next):
    """Optional HTTP Basic auth for a publicly exposed portfolio dashboard."""
    if not DASHBOARD_PASSWORD or request.url.path == "/health":
        return await call_next(request)
    authorization = request.headers.get("authorization", "")
    try:
        scheme, value = authorization.split(" ", 1)
        username, password = b64decode(value, validate=True).decode().split(":", 1)
    except (ValueError, UnicodeDecodeError, binascii.Error):
        scheme, username, password = "", "", ""
    if (scheme.lower() != "basic" or
            not secrets.compare_digest(username, DASHBOARD_USER) or
            not secrets.compare_digest(password, DASHBOARD_PASSWORD)):
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="LLM Control Plane Demo"'})
    return await call_next(request)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index():
    return _HTML


@app.get("/api/health")
async def platform_health():
    """Return a fast, dashboard-friendly health snapshot for the demo landing page."""
    services = {
        "Broker": BROKER,
        "Control Plane": CONTROL_PLANE,
        "LLM Gateway": GATEWAY,
        "MCP Gateway": MCP_GATEWAY,
        "Agent Gateway": AGENT_GATEWAY,
    }

    async def probe(client: httpx.AsyncClient, name: str, base_url: str):
        try:
            response = await client.get(f"{base_url}/health")
            return {"name": name, "status": "healthy" if response.is_success else "degraded"}
        except Exception:
            return {"name": name, "status": "offline"}

    async with httpx.AsyncClient(timeout=2) as client:
        results = await asyncio.gather(*(probe(client, name, url) for name, url in services.items()))
    return {"services": results, "healthy": sum(item["status"] == "healthy" for item in results)}


# ── Routes (proxy to broker) ──────────────────────────────────────────────────

@app.get("/api/routes")
async def list_routes():
    return await _get(f"{BROKER}/v2/routes")


@app.post("/api/routes")
async def create_route(req: Request):
    body = await req.json()
    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "Route name is required")
    params = {
        "name": name,
        "provider": body.get("provider", "ollama"),
        "model": body.get("model", ""),
        "base_url": body.get("base_url") or None,
        "api_key_env": body.get("api_key_env") or None,
        "rate_limit_rpm": int(body.get("rate_limit_rpm", 60)),
        "max_tokens": int(body.get("max_tokens", 4096)),
        "temperature": float(body.get("temperature", 0.7)),
        "cost_per_1k_tokens": float(body.get("cost_per_1k_tokens", 0)),
        "fallback_route_name": body.get("fallback_route_name") or None,
    }
    # Unique instance ID so re-creating a deleted route never 409s.
    instance_id = f"ui-{name}-{__import__('time').time_ns()}"
    payload = {"service_id": "llm-route", "plan_id": "ollama-basic",
               "instance_id": instance_id, "parameters": params}
    return await _send("PUT", f"{BROKER}/v2/service_instances/{instance_id}", payload)


@app.patch("/api/routes/{name}")
async def update_route(name: str, req: Request):
    return await _send("PATCH", f"{BROKER}/v2/routes/{name}", await req.json())


@app.delete("/api/routes/{name}")
async def delete_route(name: str):
    return await _send("DELETE", f"{BROKER}/v2/routes/{name}")


# ── Usage (proxy to gateway) ──────────────────────────────────────────────────

@app.get("/api/usage")
async def usage():
    data = await _get(f"{GATEWAY}/v1/usage")
    rows = data.get("usage", []) if isinstance(data, dict) else []
    # Enrich each row with latency percentiles.
    async with httpx.AsyncClient(timeout=5) as client:
        for row in rows:
            try:
                r = await client.get(f"{GATEWAY}/v1/latency/{row['route']}")
                row["latency"] = r.json() if r.is_success else None
            except Exception:
                row["latency"] = None
    return {"usage": rows}


# ── Chat (proxy to gateway) ───────────────────────────────────────────────────

@app.post("/api/chat")
async def chat(req: Request):
    body = await req.json()
    return await _send("POST", f"{GATEWAY}/v1/chat/completions", body)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get(url: str):
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url)
    if not r.is_success:
        raise HTTPException(r.status_code, r.text)
    return r.json()


async def _send(method: str, url: str, body=None):
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.request(method, url, json=body)
    if not r.is_success and r.status_code != 204:
        raise HTTPException(r.status_code, r.text)
    try:
        return r.json()
    except Exception:
        return {}
