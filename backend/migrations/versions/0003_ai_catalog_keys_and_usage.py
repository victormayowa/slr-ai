"""AI model catalog, users' API keys, and AI usage and grounding columns

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-14 23:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (provider, model ID, label, default for new projects). Model IDs as published by each provider in September 2026;
# the catalog is maintained in the database after this migration. Prices are left unset until confirmed.
CATALOG = [
    ("anthropic", "claude-sonnet-5", "Claude Sonnet 5", False),
    ("anthropic", "claude-opus-5", "Claude Opus 5", False),
    ("anthropic", "claude-haiku-4-5-20251001", "Claude Haiku 4.5", False),
    ("gemini", "gemini-3.8-flash", "Gemini 3.8 Flash", True),
    ("gemini", "gemini-3.6-flash", "Gemini 3.6 Flash", False),
    ("openai", "gpt-5.4-mini", "GPT-5.4 mini", False),
    ("openai", "gpt-5.5", "GPT-5.5", False),
    ("qwen", "qwen-plus", "Qwen Plus", False),
    ("qwen", "qwen-max", "Qwen Max", False),
    ("kimi", "kimi-k2.6", "Kimi K2.6", False),
    ("kimi", "kimi-k3", "Kimi K3", False),
    ("deepseek", "deepseek-v4-flash", "DeepSeek V4 Flash", False),
    ("deepseek", "deepseek-v4-pro", "DeepSeek V4 Pro", False),
    ("glm", "glm-5", "GLM-5", False),
    ("glm", "glm-5.3", "GLM-5.3", False),
    ("mistral", "mistral-medium-latest", "Mistral Medium (latest)", False),
    ("mistral", "mistral-large-latest", "Mistral Large (latest)", False),
]

AI_RUN_USAGE_COLUMNS: list[tuple[str, sa.types.TypeEngine]] = [
    ("input_tokens", sa.Integer()),
    ("output_tokens", sa.Integer()),
    ("cost_usd", sa.Numeric(12, 6)),
    ("latency_ms", sa.Integer()),
    ("attempts", sa.Integer()),
    ("key_source", sa.String(length=10)),
]


def upgrade() -> None:
    ai_models = op.create_table(
        "ai_models",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("model_id", sa.String(length=100), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("input_price_per_mtok", sa.Numeric(10, 4), nullable=True),
        sa.Column("output_price_per_mtok", sa.Numeric(10, 4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "model_id", name="uq_ai_model"),
    )
    op.create_index(
        "uq_ai_models_single_default",
        "ai_models",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    op.bulk_insert(
        ai_models,
        [
            {"provider": provider, "model_id": model_id, "label": label, "enabled": True, "is_default": is_default}
            for provider, model_id, label, is_default in CATALOG
        ],
    )

    op.add_column("projects", sa.Column("ai_model_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_projects_ai_model_id", "projects", "ai_models", ["ai_model_id"], ["id"], ondelete="SET NULL"
    )
    op.execute("UPDATE projects SET ai_model_id = (SELECT id FROM ai_models WHERE is_default)")

    op.create_table(
        "user_api_keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("encrypted_key", sa.LargeBinary(), nullable=False),
        sa.Column("last_four", sa.String(length=4), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", name="uq_user_api_key"),
    )
    op.create_index(op.f("ix_user_api_keys_user_id"), "user_api_keys", ["user_id"], unique=False)

    for name, column_type in AI_RUN_USAGE_COLUMNS:
        op.add_column("ai_runs", sa.Column(name, column_type, nullable=True))
    op.add_column("screening_suggestions", sa.Column("quote_verified", sa.Boolean(), nullable=True))
    op.add_column("extraction_suggestions", sa.Column("evidence_quote", sa.Text(), nullable=True))
    op.add_column("extraction_suggestions", sa.Column("quote_verified", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("extraction_suggestions", "quote_verified")
    op.drop_column("extraction_suggestions", "evidence_quote")
    op.drop_column("screening_suggestions", "quote_verified")
    for name, _ in reversed(AI_RUN_USAGE_COLUMNS):
        op.drop_column("ai_runs", name)
    op.drop_index(op.f("ix_user_api_keys_user_id"), table_name="user_api_keys")
    op.drop_table("user_api_keys")
    op.drop_constraint("fk_projects_ai_model_id", "projects", type_="foreignkey")
    op.drop_column("projects", "ai_model_id")
    op.drop_index("uq_ai_models_single_default", table_name="ai_models", postgresql_where=sa.text("is_default"))
    op.drop_table("ai_models")
