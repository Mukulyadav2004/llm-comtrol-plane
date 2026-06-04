"""Add semantic_routing_rules table

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-01
"""
from typing import Sequence, Union
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "semantic_routing_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("intent", sa.String(128), nullable=False, unique=True, index=True),
        sa.Column("route_name", sa.String(256), nullable=False),
        sa.Column("description", sa.String(512), nullable=True),
        sa.Column("keyword_hints", postgresql.JSON, nullable=False, server_default="[]"),
        sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Seed with sensible defaults so the system works out of the box
    op.execute("""
        INSERT INTO semantic_routing_rules (id, intent, route_name, description, keyword_hints, priority, enabled)
        VALUES
          (gen_random_uuid(), 'code',     'local-llama', 'Code generation and debugging',      '["python","javascript","function","bug","error","class","import","def "]', 10, true),
          (gen_random_uuid(), 'math',     'local-llama', 'Mathematical reasoning and problems', '["calculate","equation","integral","derivative","probability","matrix"]', 9, true),
          (gen_random_uuid(), 'creative', 'local-llama', 'Creative writing and storytelling',  '["story","poem","write","creative","fiction","character","plot"]', 8, true),
          (gen_random_uuid(), 'qa',       'local-llama', 'Factual question answering',         '["what is","who is","when did","where is","how does","explain"]', 7, true),
          (gen_random_uuid(), 'general',  'local-llama', 'Default intent for everything else', '[]', 0, true)
    """)


def downgrade() -> None:
    op.drop_table("semantic_routing_rules")
