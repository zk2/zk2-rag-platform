"""A/B experiments over pipeline versions

Revision ID: 0009_ab_experiments
Revises: 0008_evals
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_ab_experiments"
down_revision: str | Sequence[str] | None = "0008_evals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ab_experiments",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "org_id",
            sa.BigInteger,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bot_id", sa.BigInteger, sa.ForeignKey("bots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        # draft | running | stopped
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("stopped_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("idx_ab_experiments_bot", "ab_experiments", ["bot_id", "status"])

    op.create_table(
        "ab_variants",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "experiment_id",
            sa.BigInteger,
            sa.ForeignKey("ab_experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        # NULL means "whatever the bot runs today" - the honest control
        sa.Column(
            "pipeline_version_id",
            sa.BigInteger,
            sa.ForeignKey("pipeline_versions.id", ondelete="CASCADE"),
        ),
        sa.Column("traffic_percent", sa.Integer, nullable=False),
        sa.Column("is_control", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )
    op.create_index("idx_ab_variants_experiment", "ab_variants", ["experiment_id"])

    op.create_table(
        "ab_assignments",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "experiment_id",
            sa.BigInteger,
            sa.ForeignKey("ab_experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # A user id, or a conversation key for anonymous traffic
        sa.Column("subject", sa.String(128), nullable=False),
        sa.Column(
            "variant_id",
            sa.BigInteger,
            sa.ForeignKey("ab_variants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("experiment_id", "subject", name="uq_ab_assignment_subject"),
    )


def downgrade() -> None:
    op.drop_table("ab_assignments")
    op.drop_table("ab_variants")
    op.drop_table("ab_experiments")
