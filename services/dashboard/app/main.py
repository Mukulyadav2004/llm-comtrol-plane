"""Tiny dashboard backend — serves the single-page UI and proxies to broker/gateway."""
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

BROKER = os.getenv("BROKER_URL", "http://broker:8000")
GATEWAY = os.getenv("GATEWAY_URL", "http://gateway:8002")

app = FastAPI(docs_url=None, redoc_url=None)

_HTML = (Path(__file__).parent / "static" / "index.html").read_text()


@app.get("/", response_class=HTMLResponse)
async def index():
    return _HTML


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
