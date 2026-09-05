"""MCP servers per organization

Revision ID: 0007_mcp_servers
Revises: 0006_pipelines
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_mcp_servers"
down_revision: str | Sequence[str] | None = "0006_pipelines"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "org_id",
            sa.BigInteger,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("url", sa.String(1000), nullable=False),
        # Fernet-encrypted, like provider keys - see ADR-0007
        sa.Column("headers_encrypted", sa.Text),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'unknown'")),
        sa.Column("last_error", sa.String(1000)),
        sa.Column("tool_count", sa.BigInteger),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("idx_mcp_servers_org", "mcp_servers", ["org_id"])


def downgrade() -> None:
    op.drop_index("idx_mcp_servers_org", table_name="mcp_servers")
    op.drop_table("mcp_servers")
