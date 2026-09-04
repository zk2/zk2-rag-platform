"""ORM model for org-scoped LLM provider credentials."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from zk2.core.db import Base


class LLMProviderConfig(Base):
    __tablename__ = "llm_providers"
    __table_args__ = (UniqueConstraint("org_id", "provider", name="uq_llm_providers_org_provider"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    provider: Mapped[str] = mapped_column(String(32))
    api_key_encrypted: Mapped[str | None] = mapped_column(String(2048))
    custom_base_url: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
