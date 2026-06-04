COMPOSE = docker compose -f infra/docker-compose.yml

.PHONY: up down build logs ps migrate shell-broker shell-gateway

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down -v

build:
	$(COMPOSE) build

logs:
	$(COMPOSE) logs -f

ps:
	$(COMPOSE) ps

migrate:
	$(COMPOSE) run --rm migrate

shell-broker:
	$(COMPOSE) exec broker bash

shell-gateway:
	$(COMPOSE) exec gateway bash

# Pull Ollama model manually if the entrypoint didn't work
pull-model:
	$(COMPOSE) exec ollama ollama pull llama3.2:1b

# Provision a test LLM route via the broker API
test-provision:
	curl -s -X PUT http://localhost:8000/v2/service_instances/route-001 \
	  -H 'Content-Type: application/json' \
	  -d '{"service_id":"llm-route","plan_id":"ollama-basic","instance_id":"route-001","parameters":{"name":"local-llama","provider":"ollama","model":"llama3.2:1b","base_url":"http://ollama:11434","rate_limit_rpm":30}}' | python3 -m json.tool

# Check provision status
test-status:
	curl -s http://localhost:8000/v2/service_instances/route-001/last_operation | python3 -m json.tool

# Send a chat request through the gateway
test-chat:
	curl -s -X POST http://localhost:8002/v1/chat/completions \
	  -H 'Content-Type: application/json' \
	  -d '{"route":"local-llama","messages":[{"role":"user","content":"Hello! Explain what an LLM Gateway is in 2 sentences."}]}' | python3 -m json.tool

# View catalog
catalog:
	curl -s http://localhost:8000/v2/catalog | python3 -m json.tool

# View current gateway config rendered by control plane
config:
	curl -s http://localhost:8001/config/gateway | python3 -m json.tool

# View usage stats
usage:
	curl -s http://localhost:8002/v1/usage | python3 -m json.tool

# ── Intelligent routing demos ────────────────────────────────────────────────

# Semantic routing — route='auto' classifies intent and picks the right route
test-auto-code:
	curl -s -X POST http://localhost:8002/v1/chat/completions \
	  -H 'Content-Type: application/json' \
	  -d '{"route":"auto","messages":[{"role":"user","content":"Write a Python function to merge two sorted lists"}]}' | python3 -m json.tool

test-auto-math:
	curl -s -X POST http://localhost:8002/v1/chat/completions \
	  -H 'Content-Type: application/json' \
	  -d '{"route":"auto","messages":[{"role":"user","content":"What is the derivative of x squared plus 3x?"}]}' | python3 -m json.tool

test-auto-general:
	curl -s -X POST http://localhost:8002/v1/chat/completions \
	  -H 'Content-Type: application/json' \
	  -d '{"route":"auto","messages":[{"role":"user","content":"Tell me something interesting about the ocean"}]}' | python3 -m json.tool

# View which intent rules are loaded in the gateway
semantic-rules:
	curl -s http://localhost:8002/v1/semantic-rules | python3 -m json.tool

# Add a new semantic rule via broker
add-semantic-rule:
	curl -s -X POST http://localhost:8000/v2/semantic-routes \
	  -H 'Content-Type: application/json' \
	  -d '{"intent":"medical","route_name":"local-llama","description":"Medical and health questions","keyword_hints":["symptom","diagnosis","treatment","doctor","medicine"],"priority":11}' | python3 -m json.tool

# View latency stats (P50/P95/P99) for a route
latency:
	curl -s http://localhost:8002/v1/latency/local-llama | python3 -m json.tool

# ── Agent Gateway demos ───────────────────────────────────────────────────────

# Submit an agent task (returns session_id immediately)
test-agent:
	curl -s -X POST http://localhost:8004/v1/agents/run \
	  -H 'Content-Type: application/json' \
	  -d '{"task":"What tools do you have available? List them and describe what each one does.","route":"local-llama","max_steps":5}' | python3 -m json.tool

# Poll agent session status (replace SESSION_ID)
agent-status:
	@echo "Usage: SESSION_ID=<id> make agent-status"
	curl -s http://localhost:8004/v1/agents/$(SESSION_ID) | python3 -m json.tool

# Stream agent steps live (SSE) — open in a browser or use curl
agent-stream:
	@echo "Usage: SESSION_ID=<id> make agent-stream"
	curl -N http://localhost:8004/v1/agents/$(SESSION_ID)/stream

# ── MCP Gateway demos ─────────────────────────────────────────────────────────

# List all tools available via MCP Gateway
mcp-tools:
	curl -s http://localhost:8005/v1/tools | python3 -m json.tool

# Call a tool through MCP Gateway (example)
mcp-call:
	curl -s -X POST http://localhost:8005/v1/tools/call \
	  -H 'Content-Type: application/json' \
	  -d '{"tool":"search","arguments":{"query":"what is an LLM gateway"},"caller_id":"demo-client"}' | python3 -m json.tool

# ── Observability provisioning demos ─────────────────────────────────────────

# Provision Langfuse (replace keys with real values)
provision-langfuse:
	curl -s -X POST http://localhost:8000/v2/observability \
	  -H 'Content-Type: application/json' \
	  -d '{"name":"langfuse-prod","provider":"langfuse","project_name":"llm-gateway-demo","credentials":{"public_key":"pk-lf-xxx","secret_key":"sk-lf-xxx","host":"https://cloud.langfuse.com"},"route_names":[]}' | python3 -m json.tool

# Provision LangSmith
provision-langsmith:
	curl -s -X POST http://localhost:8000/v2/observability \
	  -H 'Content-Type: application/json' \
	  -d '{"name":"langsmith-prod","provider":"langsmith","project_name":"llm-gateway-demo","credentials":{"api_key":"ls__xxx"},"route_names":[]}' | python3 -m json.tool

# Provision Braintrust
provision-braintrust:
	curl -s -X POST http://localhost:8000/v2/observability \
	  -H 'Content-Type: application/json' \
	  -d '{"name":"braintrust-prod","provider":"braintrust","project_name":"llm-gateway-demo","credentials":{"api_key":"bt-xxx"},"route_names":[]}' | python3 -m json.tool

# List all provisioned observability configs
list-observability:
	curl -s http://localhost:8000/v2/observability | python3 -m json.tool

# Check which observability backends the gateway is currently shipping traces to
gateway-observability:
	curl -s http://localhost:8001/config/gateway | python3 -m json.tool | python3 -c "import sys,json; cfg=json.load(sys.stdin); print(json.dumps(cfg.get('observability_configs',[]), indent=2))"
