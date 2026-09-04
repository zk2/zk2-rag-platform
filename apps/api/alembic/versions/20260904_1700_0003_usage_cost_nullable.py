"""usage_events.cost_usd becomes nullable

A model that is not in the catalog has no known price. Recording that as 0.00
under-reports spend and, once billing exists, under-charges. NULL means
"unknown", and the metadata column carries the reason.

Revision ID: 0003_usage_cost_nullable
Revises: 0002_data_layer
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_usage_cost_nullable"
down_revision: str | Sequence[str] | None = "0002_data_layer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("usage_events", "cost_usd", existing_type=sa.Numeric(10, 6), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE usage_events SET cost_usd = 0 WHERE cost_usd IS NULL")
    op.alter_column("usage_events", "cost_usd", existing_type=sa.Numeric(10, 6), nullable=False)
