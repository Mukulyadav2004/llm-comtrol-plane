import uuid
import enum
from datetime import datetime

from sqlalchemy import String, DateTime, Text, Enum as SAEnum, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class ProvisionStatus(str, enum.Enum):
    pending = "pending"
    in_progress = "in_progress"
    succeeded = "succeeded"
    failed = "failed"


class ProvisionRequest(Base):
    __tablename__ = "provision_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    plan_id: Mapped[str] = mapped_column(String(128), nullable=False)
    instance_id: Mapped[str] = mapped_column(String(256), nullable=False, unique=True, index=True)
    status: Mapped[ProvisionStatus] = mapped_column(
        SAEnum(ProvisionStatus, name="provision_status_enum"), default=ProvisionStatus.pending
    )
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    result: Mapped[dict] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    celery_task_id: Mapped[str] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
