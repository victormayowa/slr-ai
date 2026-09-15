"""Citation links for records found by citation searching

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-15 07:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "citation_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("search_run_id", sa.Integer(), nullable=False),
        sa.Column("seed_record_id", sa.Integer(), nullable=False),
        sa.Column("record_id", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["record_id"], ["records.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["search_run_id"], ["search_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["seed_record_id"], ["records.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("search_run_id", "seed_record_id", "record_id", name="uq_citation_link"),
    )
    op.create_index(op.f("ix_citation_links_project_id"), "citation_links", ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_citation_links_project_id"), table_name="citation_links")
    op.drop_table("citation_links")
