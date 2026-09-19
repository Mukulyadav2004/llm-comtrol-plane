# Compact Railway trial deployment: independent processes, one service slot.
# Production deployments should use the per-service Dockerfiles instead.
FROM python:3.11-slim

WORKDIR /app
COPY services/ ./services/
RUN pip install --no-cache-dir \
    -r services/broker/requirements.txt \
    -r services/control-plane/requirements.txt \
    -r services/gateway/requirements.txt \
    -r services/mcp-gateway/requirements.txt \
    -r services/agent-gateway/requirements.txt \
    -r services/mcp-tools/requirements.txt
COPY deploy/railway-platform.py ./deploy/railway-platform.py

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8001 8002 8004 8005 8101 8102
CMD ["python", "deploy/railway-platform.py"]
