"""Pipelines: constructible RAG graphs, versioned like bots

Revision ID: 0006_pipelines
Revises: 0005_org_settings
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_pipelines"
down_revision: str | Sequence[str] | None = "0005_org_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pipelines",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "org_id",
            sa.BigInteger,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(1000)),
        sa.Column("current_version_id", sa.BigInteger),  # FK added below
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("idx_pipelines_org", "pipelines", ["org_id"])

    op.create_table(
        "pipeline_versions",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "pipeline_id",
            sa.BigInteger,
            sa.ForeignKey("pipelines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # The whole graph as saved by the editor: nodes, edges, positions
        sa.Column("dag", postgresql.JSONB, nullable=False),
        sa.Column("label", sa.String(255)),
        sa.Column("created_by_user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("idx_pipeline_versions_pipeline", "pipeline_versions", ["pipeline_id"])
    op.create_foreign_key(
        "fk_pipelines_current_version",
        "pipelines",
        "pipeline_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "org_id",
            sa.BigInteger,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pipeline_version_id",
            sa.BigInteger,
            sa.ForeignKey("pipeline_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bot_id", sa.BigInteger, sa.ForeignKey("bots.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(16), nullable=False),  # ok | failed
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("answer", sa.Text),
        sa.Column("error", sa.String(2000)),
        sa.Column("duration_ms", sa.Integer),
        # Per-node timings: what makes a slow pipeline diagnosable in the editor
        sa.Column(
            "node_timings", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("idx_pipeline_runs_version", "pipeline_runs", ["pipeline_version_id"])
    op.create_index("idx_pipeline_runs_org_ts", "pipeline_runs", ["org_id", "created_at"])

    # A bot without a pipeline runs the built-in default
    op.add_column("bots", sa.Column("pipeline_id", sa.BigInteger))
    op.create_foreign_key(
        "fk_bots_pipeline", "bots", "pipelines", ["pipeline_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint("fk_bots_pipeline", "bots", type_="foreignkey")
    op.drop_column("bots", "pipeline_id")
    op.drop_table("pipeline_runs")
    op.drop_constraint("fk_pipelines_current_version", "pipelines", type_="foreignkey")
    op.drop_table("pipeline_versions")
    op.drop_table("pipelines")
