"""Embedding models, record embeddings, and background AI jobs

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-15 00:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# llm.providers.EMBEDDING_DIMENSIONS when this migration was written. Every embedding model below can produce it.
EMBEDDING_DIMENSIONS = 1024

# (provider, model ID, label, default for new projects). Model IDs as published in September 2026.
EMBEDDING_MODELS = [
    ("gemini", "gemini-embedding-001", "Gemini Embedding", True),
    ("openai", "text-embedding-3-small", "text-embedding-3-small", False),
    ("qwen", "text-embedding-v4", "Qwen Text Embedding v4", False),
    ("glm", "embedding-3", "GLM Embedding-3", False),
    ("mistral", "mistral-embed", "Mistral Embed", False),
]


def upgrade() -> None:
    op.add_column("ai_models", sa.Column("purpose", sa.String(length=10), server_default="chat", nullable=False))
    op.drop_index("uq_ai_models_single_default", table_name="ai_models", postgresql_where=sa.text("is_default"))
    op.create_index(
        "uq_ai_models_default_per_purpose",
        "ai_models",
        ["purpose"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    ai_models = sa.table(
        "ai_models",
        sa.column("provider", sa.String),
        sa.column("model_id", sa.String),
        sa.column("label", sa.String),
        sa.column("purpose", sa.String),
        sa.column("enabled", sa.Boolean),
        sa.column("is_default", sa.Boolean),
    )
    op.bulk_insert(
        ai_models,
        [
            {
                "provider": provider,
                "model_id": model_id,
                "label": label,
                "purpose": "embedding",
                "enabled": True,
                "is_default": is_default,
            }
            for provider, model_id, label, is_default in EMBEDDING_MODELS
        ],
    )

    op.add_column("projects", sa.Column("embedding_model_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_projects_embedding_model_id", "projects", "ai_models", ["embedding_model_id"], ["id"], ondelete="SET NULL"
    )
    op.execute(
        "UPDATE projects SET embedding_model_id = (SELECT id FROM ai_models WHERE is_default AND purpose = 'embedding')"
    )

    op.create_table(
        "record_embeddings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("record_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=130), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["record_id"], ["records.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("record_id", "model", name="uq_record_embedding_model"),
    )
    op.create_index(op.f("ix_record_embeddings_project_id"), "record_embeddings", ["project_id"], unique=False)
    op.create_index(
        "ix_record_embeddings_embedding_hnsw",
        "record_embeddings",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "ai_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("task", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("record_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("processed", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_jobs_project_id"), "ai_jobs", ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_jobs_project_id"), table_name="ai_jobs")
    op.drop_table("ai_jobs")
    op.drop_index(
        "ix_record_embeddings_embedding_hnsw",
        table_name="record_embeddings",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_index(op.f("ix_record_embeddings_project_id"), table_name="record_embeddings")
    op.drop_table("record_embeddings")
    op.drop_constraint("fk_projects_embedding_model_id", "projects", type_="foreignkey")
    op.drop_column("projects", "embedding_model_id")
    op.execute("DELETE FROM ai_models WHERE purpose = 'embedding'")
    op.drop_index("uq_ai_models_default_per_purpose", table_name="ai_models", postgresql_where=sa.text("is_default"))
    op.create_index(
        "uq_ai_models_single_default", "ai_models", ["is_default"], unique=True, postgresql_where=sa.text("is_default")
    )
    op.drop_column("ai_models", "purpose")
