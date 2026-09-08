import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Integer, Float, Boolean, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class LLMRoute(Base):
    """A provisioned LLM route — maps a name to a provider + model with policy."""

    __tablename__ = "llm_routes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True, index=True)
    # Must name a provider registered in the gateway: see
    # services/gateway/app/providers/__init__.py
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(256), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=True)
    # The NAME of an environment variable the gateway reads, never the key
    # itself. Route config is cached in Redis and served over /v2/routes and
    # /config/gateway; a credential stored here would come to rest in all
    # three in plaintext.
    api_key_env: Mapped[str] = mapped_column(String(256), nullable=True)
    # Provider-specific passthrough (top_p, seed, include_usage, ...).
    provider_options: Mapped[dict] = mapped_column(JSON, default=dict)

    # Policy
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096)
    temperature: Mapped[float] = mapped_column(Float, default=0.7)
    rate_limit_rpm: Mapped[int] = mapped_column(Integer, default=60)
    cost_per_1k_tokens: Mapped[float] = mapped_column(Float, default=0.0)
    fallback_route_name: Mapped[str] = mapped_column(String(256), nullable=True)
    guardrail_ids: Mapped[list] = mapped_column(JSON, default=list)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
