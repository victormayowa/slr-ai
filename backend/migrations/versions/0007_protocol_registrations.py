"""Protocol registrations and waivers

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-15 04:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "protocol_registrations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("registry", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("registration_id", sa.String(length=100), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("protocol_version", sa.Integer(), nullable=False),
        sa.Column("waiver_reason", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_protocol_registrations_project_id"), "protocol_registrations", ["project_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_protocol_registrations_project_id"), table_name="protocol_registrations")
    op.drop_table("protocol_registrations")
