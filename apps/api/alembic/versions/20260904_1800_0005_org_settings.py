"""Per-organization embedding settings

The embedding model was a constant in the code, so changing it would silently
orphan every vector already indexed (dense search filters on the model name).
It becomes an explicit per-org setting, and changing it is a deliberate act
with a reindex attached.

Revision ID: 0005_org_settings
Revises: 0004_source_language
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_org_settings"
down_revision: str | Sequence[str] | None = "0004_source_language"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "org_settings",
        sa.Column(
            "org_id",
            sa.BigInteger,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "embedding_provider", sa.String(32), nullable=False, server_default=sa.text("'openai'")
        ),
        sa.Column(
            "embedding_model",
            sa.String(64),
            nullable=False,
            server_default=sa.text("'text-embedding-3-small'"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    # Every existing organization keeps what the code used before
    op.execute("INSERT INTO org_settings (org_id) SELECT id FROM organizations")


def downgrade() -> None:
    op.drop_table("org_settings")
