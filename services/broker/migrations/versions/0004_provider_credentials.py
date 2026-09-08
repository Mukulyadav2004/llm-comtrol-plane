"""Replace api_key_secret with api_key_env; add provider_options.

The dropped column was a place to put a provider API key in the routes table.
Route rows are served over /v2/routes, rendered into /config/gateway and cached
in Redis, so a key stored there would come to rest in plaintext in three places
that are not secret stores. Routes now carry the NAME of an environment variable
the gateway resolves at request time, keeping the credential in the gateway's
own environment.

The column is dropped rather than migrated because any value in it is, by
definition, a secret that has already been logged and cached. Rotate those keys
and set them as environment variables on the gateway instead.

Revision ID: 0004
Revises: 0003
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("llm_routes", sa.Column("api_key_env", sa.String(256), nullable=True))
    op.add_column(
        "llm_routes",
        sa.Column("provider_options", sa.JSON(), nullable=True, server_default="{}"),
    )
    op.drop_column("llm_routes", "api_key_secret")

    # The gateway no longer has a provider named plain "openai"; the generic
    # OpenAI-format adapter covers it and every other compatible endpoint.
    op.execute(
        "UPDATE llm_routes SET provider = 'openai_compatible' WHERE provider = 'openai'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE llm_routes SET provider = 'openai' WHERE provider = 'openai_compatible'"
    )
    op.add_column("llm_routes", sa.Column("api_key_secret", sa.String(512), nullable=True))
    op.drop_column("llm_routes", "provider_options")
    op.drop_column("llm_routes", "api_key_env")
