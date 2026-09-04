"""Per-source language for BM25

`to_tsvector('english', ...)` was hardcoded, which produces nonsense stemming
for anything else. The language is detected at ingest and stored per source:
`lang` for display and filtering, `lang_config` for the Postgres text search
configuration that both indexing and querying must agree on.

Revision ID: 0004_source_language
Revises: 0003_usage_cost_nullable
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_source_language"
down_revision: str | Sequence[str] | None = "0003_usage_cost_nullable"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("lang", sa.String(8), nullable=True))
    op.add_column(
        "sources",
        sa.Column(
            "lang_config",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'simple'"),
        ),
    )
    # Existing rows were indexed with the english configuration
    op.execute("UPDATE sources SET lang = 'en', lang_config = 'english'")
    op.create_index("idx_sources_lang", "sources", ["org_id", "lang"])


def downgrade() -> None:
    op.drop_index("idx_sources_lang", table_name="sources")
    op.drop_column("sources", "lang_config")
    op.drop_column("sources", "lang")
