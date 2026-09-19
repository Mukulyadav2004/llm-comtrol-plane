# Railway deployment (portfolio demo)

This repository is a multi-service application, not a single web server. The
public URL belongs to **dashboard only**; everything else uses Railway private
networking. The local Ollama container is intentionally omitted on Railway.
The hosted demo uses Groq's OpenAI-compatible API instead.

## Current account status

The `llm-control-plane` Railway project has been created and linked locally.
On 19 September 2026, Railway refused to add Postgres with **“Free plan resource
provision limit exceeded.”** The project has no deployed services yet. Upgrade
the Railway plan or free eligible resources in your account before continuing.
Do not point this project at the existing `openweave` database: Railway private
networking is scoped to a project and environment.

## Deploy after the resource limit is lifted

1. In the linked project, add Railway **Postgres** and **Redis** databases. The
   service names below assume Railway names them `Postgres` and `Redis`.
2. Create these services from this repo. Set each service's **Root Directory**
   to the path shown, then deploy the `main` branch. The Broker image runs
   Alembic migrations before serving requests.

   | Railway service | Root directory | Internal port |
   |---|---|---:|
   | broker | `/services/broker` | 8000 |
   | worker | `/services/broker` | none |
   | control-plane | `/services/control-plane` | 8001 |
   | gateway | `/services/gateway` | 8002 |
   | mcp-tools-utility | `/services/mcp-tools` | 8100 |
   | mcp-tools-knowledge | `/services/mcp-tools` | 8100 |
   | mcp-gateway | `/services/mcp-gateway` | 8005 |
   | agent-gateway | `/services/agent-gateway` | 8004 |
   | dashboard | `/services/dashboard` | 8080 |

   Override **worker**'s start command with:

   ```text
   celery -A app.workers.celery_app worker -B --loglevel=info --concurrency=2
   ```

   `-B` runs the periodic MCP health check in the same worker for this small
   demo. Run one worker replica. Set `TOOLSET=utility` and `TOOLSET=knowledge`
   on the two tool services respectively.
3. Set variables on the services. These are Railway reference variables (paste
   the `${{...}}` syntax literally in Railway's Variables UI):

   ```text
   broker, worker:
     DATABASE_URL=postgresql+asyncpg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.PGHOST}}:${{Postgres.PGPORT}}/${{Postgres.PGDATABASE}}
     SYNC_DATABASE_URL=${{Postgres.DATABASE_URL}}
     REDIS_URL=${{Redis.REDIS_URL}}
     CELERY_BROKER_URL=${{Redis.REDIS_URL}}
     CELERY_RESULT_BACKEND=${{Redis.REDIS_URL}}
     CONTROL_PLANE_URL=http://control-plane.railway.internal:8001

   control-plane:
     BROKER_URL=http://broker.railway.internal:8000
     REDIS_URL=${{Redis.REDIS_URL}}

   gateway:
     CONTROL_PLANE_URL=http://control-plane.railway.internal:8001
     REDIS_URL=${{Redis.REDIS_URL}}
     GROQ_API_KEY=<set privately in Railway; never commit it>

   mcp-gateway:
     CONTROL_PLANE_URL=http://control-plane.railway.internal:8001
     REDIS_URL=${{Redis.REDIS_URL}}
     LLM_GATEWAY_URL=http://gateway.railway.internal:8002

   agent-gateway:
     CONTROL_PLANE_URL=http://control-plane.railway.internal:8001
     REDIS_URL=${{Redis.REDIS_URL}}
     LLM_GATEWAY_URL=http://gateway.railway.internal:8002
     MCP_GATEWAY_URL=http://mcp-gateway.railway.internal:8005

   dashboard:
     BROKER_URL=http://broker.railway.internal:8000
     CONTROL_PLANE_URL=http://control-plane.railway.internal:8001
     GATEWAY_URL=http://gateway.railway.internal:8002
     MCP_GATEWAY_URL=http://mcp-gateway.railway.internal:8005
     AGENT_GATEWAY_URL=http://agent-gateway.railway.internal:8004
     HOSTED_DEMO_SEED=1
     DASHBOARD_USER=demo
     DASHBOARD_PASSWORD=<strong unique password>
   ```

   The gateway's Groq key is already configured locally in ignored `infra/.env`.
   Transfer it into the Railway **gateway** variables using Railway's secret
   input; do not paste it into a route or GitHub. The hosted seed uses
   `openai/gpt-oss-20b`, a [Groq production model](https://console.groq.com/docs/models).
   `cost_per_1k_tokens` is a single approximate value; Groq bills input and
   output at different rates, so the UI cost is an estimate.
4. Generate a public Railway domain for **dashboard** on target port `8080`.
   Do not create public domains for Broker, gateways, databases, or tools.
   Share the Basic Auth credentials only with people you want to demo to.
5. Wait for the dashboard's five health checks to turn green. Its hosted seed
   queues a `groq-demo` route and two MCP servers. In Playground choose
   `groq-demo`, send a short message, then verify Usage has a request. If the
   seed races startup, restart dashboard after all other services are healthy.

The hosted route is limited to 5 requests/minute and 256 output tokens, but
this is still a **portfolio demo**, not a hardened public service. Monitor
Railway and Groq usage; pause the project when it is not being shown.

Railway's [Docker Compose migration guide](https://docs.railway.com/guides/docker-compose),
[monorepo guide](https://docs.railway.com/deployments/monorepo), and
[private networking guide](https://docs.railway.com/networking/private-networking)
explain the service mapping and internal addresses used here.
