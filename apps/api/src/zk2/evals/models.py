"""Evaluation storage: golden sets, runs and per-item scores."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from zk2.core.db import Base


class EvalDataset(Base):
    __tablename__ = "eval_datasets"
    __table_args__ = (Index("idx_eval_datasets_org", "org_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalItem(Base):
    __tablename__ = "eval_items"
    __table_args__ = (Index("idx_eval_items_dataset", "dataset_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("eval_datasets.id", ondelete="CASCADE")
    )
    question: Mapped[str] = mapped_column(Text)
    expected_answer: Mapped[str | None] = mapped_column(Text)
    expected_sources: Mapped[list[str]] = mapped_column(JSONB, server_default="[]", default=list)
    tags: Mapped[list[str]] = mapped_column(JSONB, server_default="[]", default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalRun(Base):
    __tablename__ = "eval_runs"
    __table_args__ = (
        Index("idx_eval_runs_dataset", "dataset_id", "created_at"),
        Index("idx_eval_runs_org", "org_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    dataset_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("eval_datasets.id", ondelete="CASCADE")
    )
    bot_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("bots.id", ondelete="SET NULL")
    )
    pipeline_version_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("pipeline_versions.id", ondelete="SET NULL")
    )
    label: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    error: Mapped[str | None] = mapped_column(String(2000))
    items_total: Mapped[int] = mapped_column(Integer, server_default="0")
    items_done: Mapped[int] = mapped_column(Integer, server_default="0")
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}", default=dict)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    is_baseline: Mapped[bool] = mapped_column(Boolean, server_default="false", default=False)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalScore(Base):
    __tablename__ = "eval_scores"
    __table_args__ = (Index("idx_eval_scores_run", "run_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("eval_runs.id", ondelete="CASCADE"))
    item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("eval_items.id", ondelete="CASCADE")
    )
    answer: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(String(2000))
    metrics: Mapped[dict[str, float]] = mapped_column(JSONB, server_default="{}", default=dict)
    retrieved: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default="[]", default=list
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
