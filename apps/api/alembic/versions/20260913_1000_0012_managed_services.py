"""Service switches

Langfuse holds about 2 GB of memory while nobody reads a trace. A super-admin
switches it on and off from the admin panel; this table is where that choice
lives, next to what the agent on the host last saw running.

No rows are seeded: the application creates each known service's row, switched
on, the first time it looks.

Revision ID: 0012_managed_services
Revises: 0011_chunking_settings
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_managed_services"
down_revision: str | Sequence[str] | None = "0011_chunking_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "managed_services",
        sa.Column("name", sa.String(32), primary_key=True),
        sa.Column("desired_state", sa.String(8), nullable=False, server_default=sa.text("'on'")),
        sa.Column("off_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_state", sa.String(16), nullable=True),
        sa.Column("observed_detail", sa.String(512), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "changed_by_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "desired_state IN ('on', 'off')", name="ck_managed_services_desired_state"
        ),
    )


def downgrade() -> None:
    op.drop_table("managed_services")
