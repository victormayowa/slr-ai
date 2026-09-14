"""Baseline: the users and projects tables as they existed before migrations.

Databases created by the app's old create_all() call already have these tables, so they are created only if missing.

Revision ID: 0001
Revises:
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "users" not in existing_tables:
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("first_name", sa.String()),
            sa.Column("last_name", sa.String()),
            sa.Column("email", sa.String()),
            sa.Column("institutional_email", sa.String()),
            sa.Column("orcid_id", sa.String()),
            sa.Column("hashed_password", sa.String()),
            sa.Column("position_role", sa.String()),
            sa.Column("reason_for_joining", sa.String()),
            sa.Column("institution", sa.String()),
            sa.Column("created_at", sa.DateTime()),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_users_id", "users", ["id"])
        op.create_index("ix_users_email", "users", ["email"], unique=True)
        op.create_index("ix_users_institutional_email", "users", ["institutional_email"], unique=True)
        op.create_index("ix_users_orcid_id", "users", ["orcid_id"], unique=True)

    if "projects" not in existing_tables:
        op.create_table(
            "projects",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("title", sa.String()),
            sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id")),
            sa.Column("created_at", sa.DateTime()),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_projects_id", "projects", ["id"])
        op.create_index("ix_projects_title", "projects", ["title"])


def downgrade() -> None:
    op.drop_table("projects")
    op.drop_table("users")
