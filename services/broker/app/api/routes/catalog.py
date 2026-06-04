from fastapi import APIRouter
from app.schemas.catalog import CatalogOut, ServiceOut, PlanOut

router = APIRouter(prefix="/v2/catalog", tags=["catalog"])

_CATALOG = CatalogOut(
    services=[
        ServiceOut(
            id="llm-route",
            name="LLM Route",
            description="Provision a named LLM endpoint with policy controls.",
            tags=["llm", "routing"],
            plans=[
                PlanOut(
                    id="ollama-basic",
                    name="Ollama Basic",
                    description="Route to a locally-hosted Ollama model.",
                    metadata={"provider": "ollama"},
                ),
                PlanOut(
                    id="ollama-with-guardrails",
                    name="Ollama + Guardrails",
                    description="Ollama route with input/output guardrails attached.",
                    metadata={"provider": "ollama", "guardrails": True},
                ),
            ],
        ),
        ServiceOut(
            id="mcp-server",
            name="MCP Tool Server",
            description="Register an MCP-compatible tool server for agent use.",
            tags=["mcp", "tools", "agents"],
            plans=[
                PlanOut(id="standard", name="Standard", description="HTTP MCP server registration."),
                PlanOut(id="authenticated", name="Authenticated", description="MCP server with bearer auth."),
            ],
        ),
        ServiceOut(
            id="guardrail",
            name="Guardrail Config",
            description="Create an input/output filter for LLM routes.",
            tags=["guardrails", "safety"],
            plans=[
                PlanOut(id="pii", name="PII Filter", description="Detect and redact PII in prompts/responses."),
                PlanOut(id="toxicity", name="Toxicity Filter", description="Block toxic content."),
                PlanOut(id="length", name="Length Limit", description="Enforce max token length."),
                PlanOut(id="regex", name="Regex Filter", description="Custom regex-based content filter."),
            ],
        ),
        ServiceOut(
            id="observability",
            name="Observability Backend",
            description=(
                "Connect an AI observability platform to trace LLM calls, evaluate outputs, "
                "and monitor cost/latency in production."
            ),
            tags=["observability", "tracing", "evals", "monitoring"],
            plans=[
                PlanOut(
                    id="langfuse",
                    name="Langfuse",
                    description="Open-source LLM tracing and evals. Self-hostable.",
                    metadata={"credentials_required": ["public_key", "secret_key"], "optional": ["host"]},
                ),
                PlanOut(
                    id="arize",
                    name="Arize AI",
                    description="ML observability — traces, embeddings, drift detection.",
                    metadata={"credentials_required": ["api_key", "space_key"]},
                ),
                PlanOut(
                    id="langsmith",
                    name="LangSmith",
                    description="LangChain's tracing and evaluation platform.",
                    metadata={"credentials_required": ["api_key"]},
                ),
                PlanOut(
                    id="braintrust",
                    name="Braintrust",
                    description="LLM eval, tracing, and dataset management.",
                    metadata={"credentials_required": ["api_key"]},
                ),
                PlanOut(
                    id="deepeval",
                    name="DeepEval / Confident AI",
                    description="LLM unit testing and CI/CD evals via Confident AI cloud.",
                    metadata={"credentials_required": ["api_key"]},
                ),
            ],
        ),
    ]
)


@router.get("", response_model=CatalogOut)
async def get_catalog():
    return _CATALOG
