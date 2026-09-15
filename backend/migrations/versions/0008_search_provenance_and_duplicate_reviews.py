"""Search run provenance (PRISMA-S), record identifiers, and reviewed duplicate candidates

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-15 05:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMPTY_JSON = sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.add_column("search_runs", sa.Column("connector", sa.String(length=40), nullable=True))
    op.add_column("search_runs", sa.Column("interface", sa.String(length=200), nullable=True))
    op.add_column("search_runs", sa.Column("total_available", sa.Integer(), nullable=True))
    op.add_column("search_runs", sa.Column("searched_on", sa.String(length=40), nullable=True))
    op.add_column("search_runs", sa.Column("file_format", sa.String(length=20), nullable=True))
    op.add_column(
        "search_runs",
        sa.Column("filters", postgresql.JSONB(astext_type=sa.Text()), server_default=_EMPTY_JSON, nullable=False),
    )
    op.add_column(
        "records",
        sa.Column("identifiers", postgresql.JSONB(astext_type=sa.Text()), server_default=_EMPTY_JSON, nullable=False),
    )
    op.add_column("records", sa.Column("url", sa.String(length=1000), server_default="", nullable=False))
    op.create_table(
        "duplicate_reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("record_id", sa.Integer(), nullable=False),
        sa.Column("other_record_id", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["other_record_id"], ["records.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["record_id"], ["records.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "record_id", "other_record_id", name="uq_duplicate_review"),
    )


def downgrade() -> None:
    op.drop_table("duplicate_reviews")
    op.drop_column("records", "url")
    op.drop_column("records", "identifiers")
    for name in ("filters", "file_format", "searched_on", "total_available", "interface", "connector"):
        op.drop_column("search_runs", name)
