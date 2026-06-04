import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Boolean, JSON, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class GuardrailConfig(Base):
    """Guardrail — input/output filter applied to LLM routes."""

    __tablename__ = "guardrail_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True, index=True)
    guardrail_type: Mapped[str] = mapped_column(String(64), nullable=False)  # pii | toxicity | length | regex
    description: Mapped[str] = mapped_column(Text, nullable=True)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    apply_on_input: Mapped[bool] = mapped_column(Boolean, default=True)
    apply_on_output: Mapped[bool] = mapped_column(Boolean, default=True)
    action_on_violation: Mapped[str] = mapped_column(String(32), default="block")  # block | redact | warn
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
