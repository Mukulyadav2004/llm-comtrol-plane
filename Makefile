COMPOSE = docker compose -f infra/docker-compose.yml

.PHONY: up down build logs ps migrate shell-broker shell-gateway ui models

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

# Open the admin dashboard (Routes / Usage / Playground)
ui:
	@echo "Dashboard → http://localhost:8080"
	@command -v open >/dev/null && open http://localhost:8080 || true

# List models the OpenAI-compatible way (each route is a 'model')
models:
	curl -s http://localhost:8002/v1/models | python3 -m json.tool

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

# ── MCP tool servers + MCP Gateway demos ──────────────────────────────────────

# Register both MCP tool servers with the broker (utility + knowledge toolsets).
# The broker health-checks each server and notifies the control plane, so the
# registry/gateway discover the tools on their next poll.
register-mcp-tools:
	curl -s -X PUT http://localhost:8000/v2/service_instances/mcp-utility \
	  -H 'Content-Type: application/json' \
	  -d '{"service_id":"mcp-server","plan_id":"standard","instance_id":"mcp-utility","parameters":{"name":"utility-tools","endpoint_url":"http://mcp-tools-utility:8100","description":"Math, time, unit and text utilities","capabilities":["calculator","current_datetime","unit_convert","random_number","text_stats"],"tags":{"category":"utility"},"health_check_path":"/health"}}' | python3 -m json.tool
	curl -s -X PUT http://localhost:8000/v2/service_instances/mcp-knowledge \
	  -H 'Content-Type: application/json' \
	  -d '{"service_id":"mcp-server","plan_id":"standard","instance_id":"mcp-knowledge","parameters":{"name":"knowledge-tools","endpoint_url":"http://mcp-tools-knowledge:8100","description":"Web search and Wikipedia lookup","capabilities":["web_search","wikipedia"],"tags":{"category":"knowledge"},"health_check_path":"/health"}}' | python3 -m json.tool

# List all discovered tools (name + description + schema) via the MCP Gateway
mcp-tools:
	curl -s http://localhost:8005/v1/tools | python3 -m json.tool

# Call a specific tool through the MCP Gateway
mcp-call:
	curl -s -X POST http://localhost:8005/v1/tools/call \
	  -H 'Content-Type: application/json' \
	  -d '{"tool":"calculator","arguments":{"expression":"(240*15)/100"},"caller_id":"demo-client"}' | python3 -m json.tool

# Intelligent routing — gateway picks the right tool for a natural-language request
mcp-route:
	curl -s -X POST http://localhost:8005/v1/tools/route \
	  -H 'Content-Type: application/json' \
	  -d '{"query":"who is Alan Turing"}' | python3 -m json.tool

# Full auto: route to the best tool, extract its arguments, and call it
mcp-execute:
	curl -s -X POST http://localhost:8005/v1/tools/execute \
	  -H 'Content-Type: application/json' \
	  -d '{"query":"what is 15 percent of 240","caller_id":"demo-client"}' | python3 -m json.tool

mcp-execute-knowledge:
	curl -s -X POST http://localhost:8005/v1/tools/execute \
	  -H 'Content-Type: application/json' \
	  -d '{"query":"give me a short summary of the Eiffel Tower","caller_id":"demo-client"}' | python3 -m json.tool

# Guardrail demo — register a PII-redaction guardrail, then push PII through a
# tool and confirm the gateway scrubs it (applies on both arguments and results).
mcp-guardrail-demo:
	curl -s -X PUT http://localhost:8000/v2/service_instances/gr-pii \
	  -H 'Content-Type: application/json' \
	  -d '{"service_id":"guardrail","plan_id":"pii","instance_id":"gr-pii","parameters":{"name":"pii-redact","guardrail_type":"pii","action_on_violation":"redact","apply_on_input":true,"apply_on_output":true,"config":{}}}' | python3 -m json.tool
	@echo "\nNow call a tool with PII in the arguments — watch it get redacted:"
	curl -s -X POST http://localhost:8005/v1/tools/call \
	  -H 'Content-Type: application/json' \
	  -d '{"tool":"text_stats","arguments":{"text":"contact me at jane.doe@example.com or 555-123-4567"}}' | python3 -m json.tool

# Show the SSRF guard rejecting an internal URL passed as a tool argument
mcp-ssrf-demo:
	curl -s -X POST http://localhost:8005/v1/tools/call \
	  -H 'Content-Type: application/json' \
	  -d '{"tool":"web_search","arguments":{"query":"http://169.254.169.254/latest/meta-data"}}' | python3 -m json.tool

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
