import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Integer, Float, Boolean, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class SemanticRoutingRule(Base):
    """Maps a detected intent to a preferred LLM route.

    When a client sends route='auto', the gateway classifies the request
    and picks the route whose intent matches best.
    """

    __tablename__ = "semantic_routing_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    intent: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    route_name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=True)
    # Keywords boost classification confidence when present in the prompt
    keyword_hints: Mapped[list] = mapped_column(JSON, default=list)
    priority: Mapped[int] = mapped_column(Integer, default=0)  # higher wins on tie
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
