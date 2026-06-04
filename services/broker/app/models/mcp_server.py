import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Boolean, JSON, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class MCPServer(Base):
    """A registered MCP tool server."""

    __tablename__ = "mcp_servers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    endpoint_url: Mapped[str] = mapped_column(String(512), nullable=False)
    auth_header: Mapped[str] = mapped_column(String(512), nullable=True)
    capabilities: Mapped[list] = mapped_column(JSON, default=list)  # list of tool names
    tags: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    health_check_path: Mapped[str] = mapped_column(String(256), default="/health")
    last_health_check: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    healthy: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
