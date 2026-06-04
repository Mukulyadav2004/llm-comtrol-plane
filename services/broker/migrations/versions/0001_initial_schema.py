"""Initial schema: provision_requests, llm_routes, mcp_servers, guardrail_configs

Revision ID: 0001
Revises:
Create Date: 2026-06-01
"""
from typing import Sequence, Union
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "provision_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("service_id", sa.String(128), nullable=False, index=True),
        sa.Column("plan_id", sa.String(128), nullable=False),
        sa.Column("instance_id", sa.String(256), nullable=False, unique=True, index=True),
        sa.Column("status", sa.Enum("pending", "in_progress", "succeeded", "failed", name="provision_status_enum"), nullable=False, default="pending"),
        sa.Column("parameters", postgresql.JSON, nullable=False, server_default="{}"),
        sa.Column("result", postgresql.JSON, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("celery_task_id", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    op.create_table(
        "llm_routes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False, unique=True, index=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(256), nullable=False),
        sa.Column("base_url", sa.String(512), nullable=True),
        sa.Column("api_key_secret", sa.String(512), nullable=True),
        sa.Column("max_tokens", sa.Integer, nullable=False, default=4096),
        sa.Column("temperature", sa.Float, nullable=False, default=0.7),
        sa.Column("rate_limit_rpm", sa.Integer, nullable=False, default=60),
        sa.Column("cost_per_1k_tokens", sa.Float, nullable=False, default=0.0),
        sa.Column("fallback_route_name", sa.String(256), nullable=True),
        sa.Column("guardrail_ids", postgresql.JSON, nullable=False, server_default="[]"),
        sa.Column("enabled", sa.Boolean, nullable=False, default=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "mcp_servers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False, unique=True, index=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("endpoint_url", sa.String(512), nullable=False),
        sa.Column("auth_header", sa.String(512), nullable=True),
        sa.Column("capabilities", postgresql.JSON, nullable=False, server_default="[]"),
        sa.Column("tags", postgresql.JSON, nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean, nullable=False, default=True),
        sa.Column("health_check_path", sa.String(256), nullable=False, default="/health"),
        sa.Column("last_health_check", sa.DateTime(timezone=True), nullable=True),
        sa.Column("healthy", sa.Boolean, nullable=False, default=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "guardrail_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False, unique=True, index=True),
        sa.Column("guardrail_type", sa.String(64), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("config", postgresql.JSON, nullable=False, server_default="{}"),
        sa.Column("apply_on_input", sa.Boolean, nullable=False, default=True),
        sa.Column("apply_on_output", sa.Boolean, nullable=False, default=True),
        sa.Column("action_on_violation", sa.String(32), nullable=False, default="block"),
        sa.Column("enabled", sa.Boolean, nullable=False, default=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("guardrail_configs")
    op.drop_table("mcp_servers")
    op.drop_table("llm_routes")
    op.drop_table("provision_requests")
    op.execute("DROP TYPE provision_status_enum")
