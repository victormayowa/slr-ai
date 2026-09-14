"""Organizations, project membership with roles, and account status.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_organizations_name"),
    )
    op.create_table(
        "organization_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_organization_member"),
    )
    op.create_index("ix_organization_members_user_id", "organization_members", ["user_id"])

    op.create_table(
        "project_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_member"),
    )
    op.create_index("ix_project_members_user_id", "project_members", ["user_id"])

    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
        for column in ("first_name", "last_name", "email", "hashed_password"):
            batch.alter_column(column, existing_type=sa.String(), nullable=False)
        batch.alter_column("created_at", existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True))

    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("description", sa.Text()))
        batch.add_column(sa.Column("organization_id", sa.Integer()))
        batch.create_foreign_key(
            "fk_projects_organization_id", "organizations", ["organization_id"], ["id"], ondelete="SET NULL"
        )
        batch.alter_column("title", existing_type=sa.String(), nullable=False)
        batch.alter_column("created_at", existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True))

    # Access now comes from project_members; each existing project's creator becomes its owner.
    op.execute(
        "INSERT INTO project_members (project_id, user_id, role, created_at) "
        "SELECT id, owner_id, 'owner', COALESCE(created_at, CURRENT_TIMESTAMP) FROM projects WHERE owner_id IS NOT NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.alter_column("created_at", existing_type=sa.DateTime(timezone=True), type_=sa.DateTime())
        batch.alter_column("title", existing_type=sa.String(), nullable=True)
        batch.drop_constraint("fk_projects_organization_id", type_="foreignkey")
        batch.drop_column("organization_id")
        batch.drop_column("description")

    with op.batch_alter_table("users") as batch:
        batch.alter_column("created_at", existing_type=sa.DateTime(timezone=True), type_=sa.DateTime())
        for column in ("first_name", "last_name", "email", "hashed_password"):
            batch.alter_column(column, existing_type=sa.String(), nullable=True)
        batch.drop_column("is_active")

    op.drop_index("ix_project_members_user_id", table_name="project_members")
    op.drop_table("project_members")
    op.drop_index("ix_organization_members_user_id", table_name="organization_members")
    op.drop_table("organization_members")
    op.drop_table("organizations")
