"""0002 data layer: sources, chunks, embeddings, providers, bots, conversations, usage

Revision ID: 0002_data_layer
Revises: 0001_init_auth
Create Date: 2026-05-19

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0002_data_layer"
down_revision: str | Sequence[str] | None = "0001_init_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ─── Extensions ────────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS ltree")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ─── llm_providers (per-org API keys) ──────────────────────
    op.create_table(
        "llm_providers",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "org_id",
            sa.BigInteger,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("api_key_encrypted", sa.String(2048)),
        sa.Column("custom_base_url", sa.String(512)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("org_id", "provider", name="uq_llm_providers_org_provider"),
    )

    # ─── sources (ltree tree) ──────────────────────────────────
    op.create_table(
        "sources",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "org_id",
            sa.BigInteger,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(24), nullable=False),  # directory | file | web
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("path", postgresql.LTREE, nullable=False),  # type: ignore[attr-defined]
        sa.Column(
            "status",
            sa.String(24),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("error", sa.String(2000)),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("idx_sources_org", "sources", ["org_id"])
    op.execute("CREATE INDEX idx_sources_path_gist ON sources USING gist (path)")

    # ─── source_chunks ─────────────────────────────────────────
    op.create_table(
        "source_chunks",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "source_id",
            sa.BigInteger,
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("source_id", "ordinal", name="uq_chunk_source_ordinal"),
    )
    op.create_index("idx_chunks_source", "source_chunks", ["source_id"])

    # ─── source_embeddings ────────────────────────────────────
    op.create_table(
        "source_embeddings",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "chunk_id",
            sa.BigInteger,
            sa.ForeignKey("source_chunks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("embedding", Vector, nullable=False),
        sa.UniqueConstraint("chunk_id", "model", name="uq_emb_chunk_model"),
    )
    op.create_index("idx_emb_chunk", "source_embeddings", ["chunk_id"])
    # Partial HNSW indexes for common dimensions
    op.execute(
        """
        CREATE INDEX idx_emb_1536_cos ON source_embeddings
        USING hnsw ((embedding::vector(1536)) vector_cosine_ops)
        WHERE vector_dims(embedding) = 1536
        """
    )
    op.execute(
        """
        CREATE INDEX idx_emb_3072_cos ON source_embeddings
        USING hnsw ((embedding::vector(3072)) vector_cosine_ops)
        WHERE vector_dims(embedding) = 3072
        """
    )
    op.execute(
        """
        CREATE INDEX idx_emb_768_cos ON source_embeddings
        USING hnsw ((embedding::vector(768)) vector_cosine_ops)
        WHERE vector_dims(embedding) = 768
        """
    )

    # ─── source_bm25 (tsvector for hybrid search later) ────────
    op.create_table(
        "source_bm25",
        sa.Column(
            "chunk_id",
            sa.BigInteger,
            sa.ForeignKey("source_chunks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("tsv", postgresql.TSVECTOR, nullable=False),
    )
    op.execute("CREATE INDEX idx_bm25_tsv ON source_bm25 USING gin (tsv)")

    # ─── bots ──────────────────────────────────────────────────
    op.create_table(
        "bots",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "org_id",
            sa.BigInteger,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("current_version_id", sa.BigInteger),  # FK added below
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("org_id", "name", name="uq_bot_org_name"),
    )
    op.create_index("idx_bots_org", "bots", ["org_id"])

    op.create_table(
        "bot_versions",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "bot_id",
            sa.BigInteger,
            sa.ForeignKey("bots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("system_prompt", sa.Text),
        sa.Column("llm_provider", sa.String(32), nullable=False),
        sa.Column("llm_model", sa.String(64), nullable=False),
        sa.Column("temperature", sa.Float, nullable=False, server_default=sa.text("0")),
        sa.Column("num_k", sa.Integer, nullable=False, server_default=sa.text("5")),
        sa.Column("settings", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_by_user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("idx_bot_versions_bot", "bot_versions", ["bot_id"])

    op.create_foreign_key(
        "fk_bots_current_version",
        "bots",
        "bot_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ─── bot_source (bot ↔ source many-to-many at directory level) ─
    op.create_table(
        "bot_source",
        sa.Column(
            "bot_id",
            sa.BigInteger,
            sa.ForeignKey("bots.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "source_id",
            sa.BigInteger,
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("idx_bot_source_bot", "bot_source", ["bot_id"])
    op.create_index("idx_bot_source_source", "bot_source", ["source_id"])

    # ─── conversations + messages ──────────────────────────────
    op.create_table(
        "conversations",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "bot_id",
            sa.BigInteger,
            sa.ForeignKey("bots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("title", sa.String(512)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("idx_conv_bot", "conversations", ["bot_id"])

    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "conversation_id",
            sa.BigInteger,
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),  # user | assistant | system | tool
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("tool_calls", postgresql.JSONB),
        sa.Column("sources", postgresql.JSONB),  # used chunks
        sa.Column("tokens_in", sa.Integer, server_default=sa.text("0")),
        sa.Column("tokens_out", sa.Integer, server_default=sa.text("0")),
        sa.Column("cost_usd", sa.Numeric(10, 6)),
        sa.Column("latency_ms", sa.Integer),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("idx_messages_conv_ts", "messages", ["conversation_id", "created_at"])

    # ─── usage_events (billing/analytics) ──────────────────────
    op.create_table(
        "usage_events",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "org_id",
            sa.BigInteger,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bot_id", sa.BigInteger, sa.ForeignKey("bots.id", ondelete="SET NULL")),
        sa.Column("conversation_id", sa.BigInteger),  # nullable, no FK to avoid cascade complexity
        sa.Column("event_type", sa.String(32), nullable=False),  # llm_call | embedding | retrieval
        sa.Column("provider", sa.String(32)),
        sa.Column("model", sa.String(64)),
        sa.Column("tokens_in", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("tokens_out", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("idx_usage_org_ts", "usage_events", ["org_id", "created_at"])
    op.execute(
        "CREATE INDEX idx_usage_brin_created_at ON usage_events USING BRIN (created_at)"
    )


def downgrade() -> None:
    for table in (
        "usage_events",
        "messages",
        "conversations",
        "bot_source",
    ):
        op.drop_table(table)
    op.drop_constraint("fk_bots_current_version", "bots", type_="foreignkey")
    op.drop_table("bot_versions")
    op.drop_table("bots")
    op.drop_table("source_bm25")
    op.execute("DROP INDEX IF EXISTS idx_emb_1536_cos")
    op.execute("DROP INDEX IF EXISTS idx_emb_3072_cos")
    op.execute("DROP INDEX IF EXISTS idx_emb_768_cos")
    op.drop_table("source_embeddings")
    op.drop_table("source_chunks")
    op.execute("DROP INDEX IF EXISTS idx_sources_path_gist")
    op.drop_table("sources")
    op.drop_table("llm_providers")
