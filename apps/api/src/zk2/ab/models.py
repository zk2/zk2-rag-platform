"""A/B experiments: variants of a bot's pipeline and who saw which."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from zk2.core.db import Base


class AbExperiment(Base):
    __tablename__ = "ab_experiments"
    __table_args__ = (Index("idx_ab_experiments_bot", "bot_id", "status"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    bot_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("bots.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), server_default="draft")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AbVariant(Base):
    __tablename__ = "ab_variants"
    __table_args__ = (Index("idx_ab_variants_experiment", "experiment_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    experiment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ab_experiments.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(64))
    pipeline_version_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("pipeline_versions.id", ondelete="CASCADE")
    )
    traffic_percent: Mapped[int] = mapped_column(Integer)
    is_control: Mapped[bool] = mapped_column(Boolean, server_default="false", default=False)


class AbAssignment(Base):
    __tablename__ = "ab_assignments"
    __table_args__ = (
        UniqueConstraint("experiment_id", "subject", name="uq_ab_assignment_subject"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    experiment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ab_experiments.id", ondelete="CASCADE")
    )
    subject: Mapped[str] = mapped_column(String(128))
    variant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ab_variants.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
