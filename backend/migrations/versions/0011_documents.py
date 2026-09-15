"""Full-text documents, their parsed spans, and retrieval attempts

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-15 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("record_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("origin", sa.String(length=20), nullable=False),
        sa.Column("source_url", sa.String(length=1000), server_default="", nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("license", sa.String(length=100), server_default="", nullable=False),
        sa.Column("oa_status", sa.String(length=20), server_default="", nullable=False),
        sa.Column("version", sa.String(length=40), server_default="", nullable=False),
        sa.Column("parse_status", sa.String(length=20), nullable=False),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.Column("parser", sa.String(length=60), server_default="", nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("uploaded_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["record_id"], ["records.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("record_id", "sha256", name="uq_document_record_file"),
    )
    op.create_index(op.f("ix_documents_project_id"), "documents", ["project_id"], unique=False)
    op.create_index(op.f("ix_documents_record_id"), "documents", ["record_id"], unique=False)
    op.create_table(
        "document_spans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("section", sa.String(length=500), server_default="", nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("label", sa.String(length=100), server_default="", nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_document_spans_document_id"), "document_spans", ["document_id"], unique=False)
    op.create_table(
        "fulltext_retrievals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("record_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("requested_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["record_id"], ["records.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_fulltext_retrievals_project_id"), "fulltext_retrievals", ["project_id"], unique=False)
    op.create_index(op.f("ix_fulltext_retrievals_record_id"), "fulltext_retrievals", ["record_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_fulltext_retrievals_record_id"), table_name="fulltext_retrievals")
    op.drop_index(op.f("ix_fulltext_retrievals_project_id"), table_name="fulltext_retrievals")
    op.drop_table("fulltext_retrievals")
    op.drop_index(op.f("ix_document_spans_document_id"), table_name="document_spans")
    op.drop_table("document_spans")
    op.drop_index(op.f("ix_documents_record_id"), table_name="documents")
    op.drop_index(op.f("ix_documents_project_id"), table_name="documents")
    op.drop_table("documents")
