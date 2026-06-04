"""Add observability_configs table

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-01
"""
from typing import Sequence, Union
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "observability_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False, unique=True, index=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("project_name", sa.String(256), nullable=False),
        sa.Column("remote_project_id", sa.String(512), nullable=True),
        sa.Column("credentials", postgresql.JSON, nullable=False, server_default="{}"),
        sa.Column("route_names", postgresql.JSON, nullable=False, server_default="[]"),
        sa.Column("endpoint_url", sa.String(512), nullable=True),
        sa.Column("metadata", postgresql.JSON, nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean, nullable=False, default=True),
        sa.Column("provisioned", sa.Boolean, nullable=False, default=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("observability_configs")
