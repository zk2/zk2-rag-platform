"""Chunking becomes a setting

How a document is cut decides what retrieval can ever find, so it belongs
next to the embedding model rather than buried in the code as a constant.
It lives on the organization, not on a bot: sources are shared between bots,
and one corpus can only be cut one way.

Revision ID: 0011_chunking_settings
Revises: 0010_key_source
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_chunking_settings"
down_revision: str | Sequence[str] | None = "0010_key_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "org_settings",
        sa.Column("chunk_size", sa.Integer(), nullable=False, server_default=sa.text("400")),
    )
    op.add_column(
        "org_settings",
        sa.Column("chunk_overlap", sa.Integer(), nullable=False, server_default=sa.text("40")),
    )
    op.add_column(
        "org_settings",
        sa.Column(
            "chunk_strategy",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'semantic'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("org_settings", "chunk_strategy")
    op.drop_column("org_settings", "chunk_overlap")
    op.drop_column("org_settings", "chunk_size")
