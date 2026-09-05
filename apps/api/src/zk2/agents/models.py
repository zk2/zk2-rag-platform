"""MCP servers an organization has connected."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from zk2.core.db import Base


class McpServer(Base):
    __tablename__ = "mcp_servers"
    __table_args__ = (Index("idx_mcp_servers_org", "org_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(128))
    url: Mapped[str] = mapped_column(String(1000))
    # Auth headers are a credential like any other: Fernet, never returned
    headers_encrypted: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(default=True)
    status: Mapped[str] = mapped_column(String(16), server_default="unknown")
    last_error: Mapped[str | None] = mapped_column(String(1000))
    tool_count: Mapped[int | None] = mapped_column(BigInteger)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
