"""ORM models for sources, chunks, embeddings."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from zk2.core.db import Base
from zk2.core.types import Ltree


class SourceType(StrEnum):
    DIRECTORY = "directory"
    FILE = "file"
    WEB = "web"


class SourceStatus(StrEnum):
    PENDING = "pending"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (Index("idx_sources_org", "org_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    type: Mapped[str] = mapped_column(String(24))
    name: Mapped[str] = mapped_column(String(512))
    path: Mapped[str] = mapped_column(Ltree())
    status: Mapped[str] = mapped_column(String(24), server_default="pending")
    error: Mapped[str | None] = mapped_column(String(2000))
    # NOTE: `metadata` is reserved by SQLAlchemy on Base — map via Column name
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, server_default="{}", default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceChunk(Base):
    __tablename__ = "source_chunks"
    __table_args__ = (
        UniqueConstraint("source_id", "ordinal", name="uq_chunk_source_ordinal"),
        Index("idx_chunks_source", "source_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sources.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, server_default="{}", default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceEmbedding(Base):
    __tablename__ = "source_embeddings"
    __table_args__ = (
        UniqueConstraint("chunk_id", "model", name="uq_emb_chunk_model"),
        Index("idx_emb_chunk", "chunk_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chunk_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_chunks.id", ondelete="CASCADE")
    )
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[list[float]] = mapped_column(Vector)


class SourceBM25(Base):
    __tablename__ = "source_bm25"

    chunk_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_chunks.id", ondelete="CASCADE"), primary_key=True
    )
    tsv: Mapped[str] = mapped_column(TSVECTOR)
