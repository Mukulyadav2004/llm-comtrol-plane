# LLM Control Plane — Architecture

A self-hosted, enterprise-grade AI gateway platform. Six FastAPI microservices split into a
**control plane** (declare/distribute config) and a **data plane** (serve live LLM + tool traffic),
backed by PostgreSQL, Redis, Ollama, and a Prometheus/Grafana + tracing observability stack.

This mirrors the classic "broker → control plane → fleet" shape of the reference diagram, but for
LLM/agent infrastructure instead of Envoy edge proxies.

---

## System flow

```mermaid
flowchart LR
    Client(["Client / Operator"])
    EndUser(["End User"])

    %% ───────────── Broker (Open Service Broker) ─────────────
    subgraph BROKER["Broker — Open Service Broker · :8000"]
        direction TB
        BAPI["FastAPI<br/>catalog · provision"]
        BAPIcfg["Resource APIs<br/>routes · mcp · guardrails<br/>semantic-routes · observability"]
        PG[("PostgreSQL 16<br/>source of truth")]
        REDQ{{"Redis<br/>Celery broker"}}
        subgraph PROV["Provisioning tasks (Celery)"]
            direction TB
            W["Worker"]
            BEAT["celery-beat<br/>(scheduled)"]
            T1["create LLM route"]
            T2["register MCP server"]
            T3["apply guardrail / rule"]
        end
        BAPI --> PG
        BAPIcfg --> PG
        BAPI --> REDQ --> W
        BEAT --> W
        W --> T1 & T2 & T3
        W --> PG
    end

    %% ───────────── Control Plane ─────────────
    subgraph CP["Control Plane — Config Distribution · :8001"]
        direction TB
        CTX["Context loader<br/>pulls /v2/* from Broker"]
        RCACHE[("Redis<br/>context cache (TTL)")]
        TMPL["Jinja2 Templates<br/>gateway_config.j2<br/>mcp_registry.j2"]
        REFRESH["refresh loop<br/>(periodic invalidate)"]
        CFG["/config/* endpoints"]
        CTX --> RCACHE
        CTX --> TMPL --> CFG
        REFRESH --> CTX
    end

    %% ───────────── Data Plane ─────────────
    subgraph DP["Data Plane (polls Control Plane for live config)"]
        direction TB
        AG["Agent Gateway · :8004<br/>ReAct loop (Reason+Act)<br/>step + token budgets"]
        LG["LLM Gateway · :8002<br/>route → rate limit → guardrails<br/>→ provider → cost/latency"]
        MG["MCP Gateway · :8005<br/>auth · per-tool rate limit · trace"]
        MR["MCP Registry · :8003<br/>discover + proxy tool servers"]
    end

    %% ───────────── Backends ─────────────
    OLLAMA["Ollama<br/>local LLM"]
    MCPS["Registered<br/>MCP tool servers"]

    %% ───────────── Observability ─────────────
    subgraph OBS["Observability"]
        direction TB
        PROM["Prometheus"]
        GRAF["Grafana"]
        TRACE["Tracing adapters<br/>Langfuse · LangSmith · Braintrust<br/>Arize · DeepEval"]
        PROM --> GRAF
    end

    %% Control flow: declare config
    Client --> BAPI
    Client --> BAPIcfg
    CTX -. "GET /v2/*" .-> BAPIcfg

    %% Data plane pulls rendered config
    AG -. poll /config .-> CFG
    LG -. poll /config .-> CFG
    MG -. poll /config .-> CFG
    MR -. poll /config .-> CFG

    %% Live request path
    EndUser --> AG
    AG -- "THINK: /v1/chat/completions" --> LG
    AG -- "ACT: /v1/tools/call" --> MG
    LG --> OLLAMA
    MG --> MR --> MCPS

    %% Telemetry
    LG -. fire-and-forget trace .-> TRACE
    MG -. tool trace .-> TRACE
    LG --> PROM
    MG --> PROM
    AG --> PROM
    BAPI --> PROM
```

---

## Request lifecycle (LLM Gateway `/v1/chat/completions`)

```mermaid
flowchart TB
    A["Incoming request<br/>route='auto' or explicit"] --> B{semantic<br/>classification?}
    B -- "route=auto" --> C["Classifier<br/>keyword scan → Ollama fallback"]
    B -- explicit --> D["resolve route"]
    C --> D
    D --> E["Rate limit<br/>(Redis sliding window)"]
    E --> F["Input guardrails<br/>PII · toxicity · regex · keyword"]
    F --> G{fallback<br/>configured?}
    G -- yes --> H["Hedged call<br/>primary + secondary in parallel"]
    G -- no --> I["Direct provider call → Ollama"]
    H --> J["Record latency (rolling P95)"]
    I --> J
    J --> K["Output guardrails"]
    K --> L["Cost tracking<br/>tokens → est. USD (Redis)"]
    L --> M["Dispatch trace<br/>(fire-and-forget)"]
    M --> N["Response"]
```

---

## Deployment / Infrastructure

```mermaid
flowchart LR
    subgraph LOCAL["Docker Compose (local / single host)"]
        direction TB
        SVC["6 services + worker + beat"]
        INFRA["postgres · redis · ollama<br/>prometheus · grafana"]
        MIG["migrate (alembic upgrade head)"]
    end

    subgraph K8S["Helm chart (Kubernetes)"]
        direction TB
        DEP["Deployments<br/>broker · worker · gateway · …"]
        VALS["values.yaml"]
    end

    MIG --> SVC
    SVC --> INFRA
    VALS --> DEP
```

---

## Service map

| Service | Port | Role |
|---|---|---|
| **Broker** | 8000 | Open Service Broker. Stores routes, guardrails, semantic rules, MCP registrations, observability configs in PostgreSQL. Async provisioning via Celery + Redis. |
| **Control Plane** | 8001 | Reads from Broker, renders Jinja2 config templates, caches in Redis, serves live config; periodic refresh. |
| **LLM Gateway** | 8002 | Core LLM proxy to Ollama: semantic routing → rate limit → input guardrails → (hedged/fallback) call → output guardrails → cost + latency → trace dispatch. |
| **MCP Registry** | 8003 | Discovers + proxies registered MCP tool servers (refreshes every 60s). |
| **Agent Gateway** | 8004 | Orchestrates ReAct loops; calls LLM Gateway to *think*, MCP Gateway to *act*; enforces step + token budgets. |
| **MCP Gateway** | 8005 | Auth, per-tool rate limiting, tracing on every tool call before forwarding to MCP servers. |

**Key design patterns:** dynamic config (each plane polls the one above it) · semantic intent classification (keyword first, LLM fallback) · hedged requests (primary + secondary in parallel, take the winner) · guardrails on both input and output of every LLM call · fire-and-forget tracing to multiple observability backends.
