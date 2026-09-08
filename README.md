# LLM Control Plane

A self-hosted AI gateway you run on your own infrastructure. Configure routes, guardrails, and tool integrations once — every request goes through a consistent pipeline with rate limiting, cost tracking, and tracing.

---

## How it works

```
  You
   │
   ▼
┌──────────────┐    declares config    ┌─────────────────┐
│    Broker    │ ──────────────────► │  Control Plane  │
│   :8000      │                      │     :8001       │
│              │    stores in          │                 │
│  catalog +   │    PostgreSQL         │  renders config │
│  provisioner │                      │  caches in Redis│
└──────────────┘                      └────────┬────────┘
                                               │
                              polls for config │
                     ┌─────────────────────────┤
                     │             │           │
                     ▼             ▼           ▼
              ┌────────────┐ ┌──────────┐ ┌──────────────┐
              │    LLM     │ │   MCP    │ │    Agent     │
              │  Gateway   │ │ Registry │ │   Gateway    │
              │   :8002    │ │  :8003   │ │    :8004     │
              │            │ │          │ │              │
              │ route →    │ │ discover │ │ ReAct loop:  │
              │ rate limit │ │ + proxy  │ │ think → act  │
              │ guardrails │ │  tools   │ │ → think …    │
              │ → Ollama   │ └────┬─────┘ └──────┬───────┘
              └────────────┘     │               │
                     ▲           ▼               ▼
                     │    ┌────────────┐  calls LLM Gateway
                     │    │    MCP     │  calls MCP Gateway
                     │    │  Gateway   │
                     │    │   :8005    │
                     │    │            │
                     │    │ auth + rate│
                     └────│ limit tool │
                          │   calls    │
                          └────────────┘
```

**Data flows top-to-bottom:** Broker holds your config → Control Plane renders and distributes it → the three gateways pull it and serve live traffic.

**An agent request** hits Agent Gateway, which thinks via LLM Gateway and acts via MCP Gateway. Both gateways enforce rate limits, guardrails, and emit traces.

---

## Services

| Service | Port | Does |
|---|---|---|
| Broker | 8000 | Stores routes, guardrails, MCP registrations in Postgres. Async provisioning via Celery. |
| Control Plane | 8001 | Reads from Broker, renders config templates, serves them to the data plane. |
| LLM Gateway | 8002 | Semantic routing → rate limit → guardrails → Ollama → cost + trace. |
| MCP Registry | 8003 | Discovers and proxies registered tool servers. |
| Agent Gateway | 8004 | Orchestrates ReAct loops with step and token budgets. |
| MCP Gateway | 8005 | Auth, rate limiting, and tracing for every tool call. |
| Dashboard | 8080 | Web admin UI — manage routes, watch live usage, and a chat playground. |

---

## Dashboard (web UI)

Once the stack is up, open **http://localhost:8080** (`make ui`). No curl required:

- **Routes** — list, create (form), enable/disable, and delete routes.
- **Usage** — live cost, tokens, requests, and P50/P95/P99 latency per route (auto-refreshes).
- **Playground** — chat through the gateway; pick a route or use `auto` for semantic routing.

The dashboard is a small FastAPI backend-for-frontend serving a single static
page. It reaches the other services over the internal Docker network, so backend
ports stay off the browser and there's no CORS to configure.

---

## OpenAI-compatible API

The gateway speaks the OpenAI Chat Completions format, so existing apps and the
official SDKs work by just pointing `base_url` at the gateway and using a route
name as the `model`:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8002/v1", api_key="sk-anything")
resp = client.chat.completions.create(
    model="local-llama",                 # = your route name
    messages=[{"role": "user", "content": "Hello!"}],
)
print(resp.choices[0].message.content)
```

### Streaming

`stream: true` returns server-sent events in OpenAI's `chat.completion.chunk`
format, terminated by `data: [DONE]`:

```python
for chunk in client.chat.completions.create(
    model="local-llama",
    messages=[{"role": "user", "content": "Count to five"}],
    stream=True,
    stream_options={"include_usage": True},   # optional final usage frame
):
    print(chunk.choices[0].delta.content or "", end="")
```

Route resolution, rate limiting and input guardrails all run *before* the
response body opens, so a rate-limited or unroutable streaming request still
returns a real `429`/`502` rather than a truncated stream.

Output guardrails run incrementally against a hold-back buffer, so a pattern
split across token boundaries (`"al" | "ice@exa" | "mple.com"`) is still caught.
The cost is that the client trails the provider by up to 64 characters. Streamed
requests are not hedged and do not fail over — both need a second response body,
and the first one is already on the wire.

- `GET /v1/models` lists every route as a "model" (`make models`).
- Auth is **off by default** (any key works). To lock it down, set
  `GATEWAY_REQUIRE_AUTH=true` and `GATEWAY_API_KEYS=key1,key2` on the gateway.
- The legacy `{"route": "..."}` request shape still works.

---

## Run it

**Prerequisites:** Docker + Docker Compose, Make

```bash
# 1. Build images
make build

# 2. Start everything (Postgres, Redis, Ollama + all 6 services)
make up

# 3. Run database migrations
make migrate

# 4. Pull the default model into Ollama (first time only, takes ~1 min)
make pull-model
```

Then open the dashboard:

```bash
make ui   # → http://localhost:8080
```

Check everything is healthy:

```bash
make ps
make logs
```

---

## Try it out

```bash
# Provision a route (tells the Broker to set up an LLM route)
make test-provision

# Wait a moment, then check it's ready
make test-status

# Send a chat request through the LLM Gateway
make test-chat

# Let the gateway pick the right route automatically (semantic routing)
make test-auto-code      # "Write a Python function to merge two sorted lists"
make test-auto-math      # "What is the derivative of x squared?"

# Run an agent task
make test-agent          # returns a session_id

# Stream the agent's steps live
SESSION_ID=<id> make agent-stream

# See what tools are available via MCP
make mcp-tools

# View cost / usage stats
make usage
make latency
```

---

## Tests

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r services/gateway/requirements.txt -r requirements-dev.txt
cd services/gateway && pytest tests -v      # or, from the repo root: make test
```

The suite imports the gateway package, so it needs the service's runtime
dependencies as well as the test-only ones.

71 tests covering the router's fallback and cycle handling, guardrail
redaction placement, rate-limit window semantics, cost math, token accounting,
and the SSE contract end-to-end. They run against `fakeredis`, so no services
need to be up. CI runs them on every push, alongside a build of all eight
images.

---

## Observability

Prometheus scrapes all services. Grafana runs at `http://localhost:3000`.

To ship traces to an external backend:

```bash
make provision-langfuse      # or langsmith / braintrust
make gateway-observability   # confirm the gateway picked it up
```

---

## Known limitations

Being explicit about what this does *not* do yet:

- **One provider.** Only Ollama is wired into the gateway's provider map. The
  Broker's schema accepts `openai`/`anthropic`/`google`, but provisioning one of
  those produces a route the gateway will reject at request time.
- **Cost is a blended rate.** `cost_per_1k_tokens` is a single number applied to
  prompt + completion together; real pricing separates input, output and cached
  tokens.
- **Cost is counters, not a ledger.** Usage lives in Redis counters, so there is
  no per-request spend log, no attribution to a key or team, and no history that
  survives a flush.
- **No budget enforcement.** Spend is observed, never capped. Rate limiting is
  RPM only — there is no TPM limit.
- **No response caching, retries, or deployment pools.** A route maps to exactly
  one model at one URL.
- **No tool/function calling.** The chat endpoint does not accept `tools`, so
  MCP tools are reachable only through the Agent Gateway's ReAct loop.
- **Guardrails are regex-based.** A useful backstop, not a substitute for a real
  PII or prompt-injection engine.

---

## Security defaults

The out-of-the-box configuration is tuned for local development and is **not**
safe to expose:

| Setting | Default | For anything real |
|---|---|---|
| `GATEWAY_REQUIRE_AUTH` | `false` — any key works | `true`, with `GATEWAY_API_KEYS` set |
| `GATEWAY_CORS_ORIGINS` | `*` | your dashboard's origin only |
| `GATEWAY_JWT_SECRET` | `change-me-in-production` | a real secret from your secret store |

---

## Tear down

```bash
make down    # stops and removes containers + volumes
```
