# LLM Control Plane

A self-hosted AI gateway you run on your own infrastructure. Configure routes, guardrails, and tool integrations once — every request goes through a consistent pipeline with rate limiting, cost tracking, and tracing.

---

## Workin

```
  USER
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

## Observability

Prometheus scrapes all services. Grafana runs at `http://localhost:3000`.

To ship traces to an external backend:

```bash
make provision-langfuse      # or langsmith / braintrust
make gateway-observability   # confirm the gateway picked it up
```

---

## Tear down

```bash
make down    # stops and removes containers + volumes
```
