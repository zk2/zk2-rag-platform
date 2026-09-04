"""ORM models for bots, bot_versions, bot_source, conversations, messages."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from zk2.core.db import Base


class Bot(Base):
    __tablename__ = "bots"
    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_bot_org_name"),
        Index("idx_bots_org", "org_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(255))
    current_version_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("bot_versions.id", ondelete="SET NULL")
    )
    # None means the built-in default pipeline
    pipeline_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("pipelines.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BotVersion(Base):
    __tablename__ = "bot_versions"
    __table_args__ = (Index("idx_bot_versions_bot", "bot_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bot_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("bots.id", ondelete="CASCADE"))
    system_prompt: Mapped[str | None] = mapped_column(Text)
    llm_provider: Mapped[str] = mapped_column(String(32))
    llm_model: Mapped[str] = mapped_column(String(64))
    temperature: Mapped[float] = mapped_column(Float, server_default="0")
    num_k: Mapped[int] = mapped_column(Integer, server_default="5")
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}", default=dict)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BotSource(Base):
    __tablename__ = "bot_source"
    __table_args__ = (
        Index("idx_bot_source_bot", "bot_id"),
        Index("idx_bot_source_source", "source_id"),
    )

    bot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("bots.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True
    )


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("idx_conv_bot", "bot_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bot_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("bots.id", ondelete="CASCADE"))
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    title: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("idx_messages_conv_ts", "conversation_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    tool_calls: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    sources: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    tokens_in: Mapped[int | None] = mapped_column(Integer, server_default="0")
    tokens_out: Mapped[int | None] = mapped_column(Integer, server_default="0")
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
