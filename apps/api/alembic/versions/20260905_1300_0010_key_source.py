"""Record which key paid for a call

Deployment-wide fallback keys are a courtesy with a limit; an organization's
own keys are unlimited. Telling the two apart has to be a column, not a
metadata field, because the allowance check reads it on every turn.

Revision ID: 0010_key_source
Revises: 0009_ab_experiments
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_key_source"
down_revision: str | Sequence[str] | None = "0009_ab_experiments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "usage_events",
        sa.Column("key_source", sa.String(8), nullable=False, server_default=sa.text("'org'")),
    )
    # The allowance query filters on exactly this pair
    op.create_index(
        "idx_usage_events_system_keys",
        "usage_events",
        ["org_id", "created_at"],
        postgresql_where=sa.text("key_source = 'system'"),
    )


def downgrade() -> None:
    op.drop_index("idx_usage_events_system_keys", table_name="usage_events")
    op.drop_column("usage_events", "key_source")
