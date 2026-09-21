"""How many possible duplicates a plan reviews at a time

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-21

Reviewing possible duplicates is work the platform does for the customer, so how much of it happens at once follows
the plan: ten pairs on the free plan, a hundred on the paid ones, and no limit for institutions. Administrators can
change these in the plans, like any other limit.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BATCHES = (("free", "10"), ("researcher", "100"), ("team", "100"), ("institution", "null"))


def upgrade() -> None:
    for code, batch in BATCHES:
        op.execute(
            f"UPDATE plans SET limits = jsonb_set(limits, '{{duplicate_batch}}', '{batch}') WHERE code = '{code}'"
        )


def downgrade() -> None:
    op.execute("UPDATE plans SET limits = limits - 'duplicate_batch'")
