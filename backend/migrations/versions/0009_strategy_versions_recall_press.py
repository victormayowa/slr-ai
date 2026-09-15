"""Search strategy versions, recall checks, and PRESS peer reviews

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-15 06:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("search_strategies", sa.Column("version", sa.Integer(), server_default="1", nullable=False))
    op.add_column("search_runs", sa.Column("strategy_version", sa.Integer(), nullable=True))
    op.create_table(
        "search_strategy_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("strategy_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("database", sa.String(length=100), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["strategy_id"], ["search_strategies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("strategy_id", "version", name="uq_search_strategy_version"),
    )
    op.execute(
        "INSERT INTO search_strategy_versions (strategy_id, version, database, query, created_at) "
        "SELECT id, 1, database, query, created_at FROM search_strategies"
    )
    op.create_table(
        "recall_checks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("strategy_id", sa.Integer(), nullable=False),
        sa.Column("strategy_version", sa.Integer(), nullable=False),
        sa.Column("connector", sa.String(length=40), nullable=False),
        sa.Column("seeds", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("found", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["strategy_id"], ["search_strategies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_recall_checks_project_id"), "recall_checks", ["project_id"], unique=False)
    op.create_table(
        "press_reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("strategy_id", sa.Integer(), nullable=True),
        sa.Column("strategy_version", sa.Integer(), nullable=True),
        sa.Column("reviewer_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column(
            "answers", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("overall", sa.String(length=20), nullable=True),
        sa.Column("comment", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["strategy_id"], ["search_strategies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_press_reviews_project_id"), "press_reviews", ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_press_reviews_project_id"), table_name="press_reviews")
    op.drop_table("press_reviews")
    op.drop_index(op.f("ix_recall_checks_project_id"), table_name="recall_checks")
    op.drop_table("recall_checks")
    op.drop_table("search_strategy_versions")
    op.drop_column("search_runs", "strategy_version")
    op.drop_column("search_strategies", "version")
