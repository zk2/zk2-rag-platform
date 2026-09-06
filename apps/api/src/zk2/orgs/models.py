"""Per-organization settings."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from zk2.core.db import Base


class OrgSettings(Base):
    __tablename__ = "org_settings"

    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    embedding_provider: Mapped[str] = mapped_column(String(32), server_default="openai")
    embedding_model: Mapped[str] = mapped_column(
        String(64), server_default="text-embedding-3-small"
    )
    # How documents are cut before they are embedded. Changing any of these
    # makes the stored chunks stale exactly the way changing the model does.
    chunk_size: Mapped[int] = mapped_column(Integer, server_default="400")
    chunk_overlap: Mapped[int] = mapped_column(Integer, server_default="40")
    chunk_strategy: Mapped[str] = mapped_column(String(16), server_default="semantic")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
