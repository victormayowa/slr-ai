"""Structured review question, FINER assessment, analysis plan, PRISMA-P sections, and protocol AI suggestions

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-15 01:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMPTY_JSON = sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.add_column("protocols", sa.Column("question", sa.Text(), server_default="", nullable=False))
    for name in ("question_elements", "finer", "analysis_plan"):
        op.add_column(
            "protocols",
            sa.Column(name, postgresql.JSONB(astext_type=sa.Text()), server_default=_EMPTY_JSON, nullable=False),
        )
    op.add_column("criteria", sa.Column("element", sa.String(length=40), nullable=True))
    op.add_column("criteria", sa.Column("source", sa.String(length=10), server_default="ai", nullable=False))

    op.create_table(
        "protocol_suggestions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("ai_run_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("section_key", sa.String(length=40), nullable=True),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ai_run_id"], ["ai_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ai_run_id"),
    )
    op.create_index(op.f("ix_protocol_suggestions_project_id"), "protocol_suggestions", ["project_id"], unique=False)

    op.create_table(
        "protocol_sections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("based_on_suggestion_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["based_on_suggestion_id"], ["protocol_suggestions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "key", name="uq_protocol_section"),
    )


def downgrade() -> None:
    op.drop_table("protocol_sections")
    op.drop_index(op.f("ix_protocol_suggestions_project_id"), table_name="protocol_suggestions")
    op.drop_table("protocol_suggestions")
    op.drop_column("criteria", "source")
    op.drop_column("criteria", "element")
    for name in ("analysis_plan", "finer", "question_elements", "question"):
        op.drop_column("protocols", name)
