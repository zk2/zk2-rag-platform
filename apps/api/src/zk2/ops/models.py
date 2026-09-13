"""ORM model for the service switches."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from zk2.core.db import Base


class ManagedService(Base):
    """One switchable group of containers: what was asked for, and what is running.

    The two halves are written by different parties. `desired_state` and
    `off_at` come from a super-admin; the `observed_*` columns come from the
    agent on the host, which is the only thing that can see the containers.
    """

    __tablename__ = "managed_services"
    __table_args__ = (
        CheckConstraint("desired_state IN ('on', 'off')", name="ck_managed_services_desired_state"),
    )

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    desired_state: Mapped[str] = mapped_column(String(8), server_default="on")
    # Set when it was switched on "for N hours": past it, the switch flips back
    off_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_state: Mapped[str | None] = mapped_column(String(16))
    observed_detail: Mapped[str | None] = mapped_column(String(512))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # None after an automatic switch-off: nobody pressed anything
    changed_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
