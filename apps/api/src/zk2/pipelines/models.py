"""Pipeline storage: a graph, its versions, and what a test run recorded."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from zk2.core.db import Base


class Pipeline(Base):
    __tablename__ = "pipelines"
    __table_args__ = (Index("idx_pipelines_org", "org_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(1000))
    current_version_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PipelineVersion(Base):
    __tablename__ = "pipeline_versions"
    __table_args__ = (Index("idx_pipeline_versions_pipeline", "pipeline_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    pipeline_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pipelines.id", ondelete="CASCADE")
    )
    dag: Mapped[dict[str, Any]] = mapped_column(JSONB)
    label: Mapped[str | None] = mapped_column(String(255))
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    __table_args__ = (
        Index("idx_pipeline_runs_version", "pipeline_version_id"),
        Index("idx_pipeline_runs_org_ts", "org_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    pipeline_version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pipeline_versions.id", ondelete="CASCADE")
    )
    bot_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("bots.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(16))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(String(2000))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    node_timings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default="[]", default=list
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
