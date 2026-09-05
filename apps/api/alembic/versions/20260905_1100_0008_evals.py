"""Evaluation datasets, runs and scores

Revision ID: 0008_evals
Revises: 0007_mcp_servers
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_evals"
down_revision: str | Sequence[str] | None = "0007_mcp_servers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eval_datasets",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "org_id",
            sa.BigInteger,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(1000)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("idx_eval_datasets_org", "eval_datasets", ["org_id"])

    op.create_table(
        "eval_items",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "dataset_id",
            sa.BigInteger,
            sa.ForeignKey("eval_datasets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("expected_answer", sa.Text),
        # Sources that ought to be retrieved for this question, by name
        sa.Column(
            "expected_sources",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("tags", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("idx_eval_items_dataset", "eval_items", ["dataset_id"])

    op.create_table(
        "eval_runs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "org_id",
            sa.BigInteger,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dataset_id",
            sa.BigInteger,
            sa.ForeignKey("eval_datasets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bot_id", sa.BigInteger, sa.ForeignKey("bots.id", ondelete="SET NULL")),
        # Which graph produced these numbers: null means the bot's own pipeline
        sa.Column(
            "pipeline_version_id",
            sa.BigInteger,
            sa.ForeignKey("pipeline_versions.id", ondelete="SET NULL"),
        ),
        sa.Column("label", sa.String(255)),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("error", sa.String(2000)),
        sa.Column("items_total", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("items_done", sa.Integer, nullable=False, server_default=sa.text("0")),
        # Mean of every metric over the dataset, so a comparison is one read
        sa.Column(
            "summary", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("cost_usd", sa.Numeric(10, 6)),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("is_baseline", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_by_user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("idx_eval_runs_dataset", "eval_runs", ["dataset_id", "created_at"])
    op.create_index("idx_eval_runs_org", "eval_runs", ["org_id", "created_at"])

    op.create_table(
        "eval_scores",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "run_id",
            sa.BigInteger,
            sa.ForeignKey("eval_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "item_id",
            sa.BigInteger,
            sa.ForeignKey("eval_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("answer", sa.Text),
        sa.Column("error", sa.String(2000)),
        # metric name -> score in [0, 1], plus latency and cost alongside
        sa.Column(
            "metrics", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "retrieved", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("cost_usd", sa.Numeric(10, 6)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("idx_eval_scores_run", "eval_scores", ["run_id"])


def downgrade() -> None:
    op.drop_table("eval_scores")
    op.drop_table("eval_runs")
    op.drop_table("eval_items")
    op.drop_table("eval_datasets")
