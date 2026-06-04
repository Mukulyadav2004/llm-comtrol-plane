import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Boolean, JSON, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class ObservabilityConfig(Base):
    """Provisioned observability backend for a project.

    Each config connects a project (or set of routes) to one observability
    platform. The gateway reads these configs from the control plane and
    ships traces there after every LLM call.

    Supported providers: langfuse | arize | langsmith | braintrust | deepeval
    """

    __tablename__ = "observability_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    project_name: Mapped[str] = mapped_column(String(256), nullable=False)

    # Validated project ID returned by the provider's API after provisioning
    remote_project_id: Mapped[str] = mapped_column(String(512), nullable=True)

    # Encrypted-at-rest in prod; plaintext here for demo clarity
    credentials: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Which LLM routes this config applies to (empty = all routes)
    route_names: Mapped[list] = mapped_column(JSON, default=list)

    # Provider-specific tracing endpoint (e.g. Langfuse self-hosted URL)
    endpoint_url: Mapped[str] = mapped_column(String(512), nullable=True)

    # Extra metadata: tags, environment, dataset names, etc.
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    provisioned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
