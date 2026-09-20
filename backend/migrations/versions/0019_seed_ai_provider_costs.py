"""Starting prices for what each AI provider charges, so the engines can be offered and billed

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-20

These are list prices in US dollars per million tokens, as understood in September 2026, and they are estimates:
providers change them, and a model that costs more than this is sold at a loss. Confirm each against the provider's
own price page before charging customers, and keep them current in Admin -> AI models.

Customers pay AI_PRICE_MARKUP times these figures (four by default), so a wrong cost here is a wrong price there.
Only models with no cost recorded are filled in, so prices an operator has already entered are left alone.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# provider, model id, input per million tokens, output per million tokens. Embeddings charge for input only.
COSTS = (
    ("anthropic", "claude-opus-5", "15.00", "75.00"),
    ("anthropic", "claude-sonnet-5", "3.00", "15.00"),
    ("anthropic", "claude-haiku-4-5-20251001", "1.00", "5.00"),
    ("openai", "gpt-5.5", "1.25", "10.00"),
    ("openai", "gpt-5.4-mini", "0.25", "2.00"),
    ("gemini", "gemini-3.8-flash", "0.30", "2.50"),
    ("gemini", "gemini-3.6-flash", "0.30", "2.50"),
    ("qwen", "qwen-max", "1.60", "6.40"),
    ("qwen", "qwen-plus", "0.40", "1.20"),
    ("kimi", "kimi-k3", "1.00", "4.00"),
    ("kimi", "kimi-k2.6", "0.60", "2.50"),
    ("deepseek", "deepseek-v4-pro", "0.55", "2.20"),
    ("deepseek", "deepseek-v4-flash", "0.28", "0.42"),
    ("glm", "glm-5.3", "0.90", "3.00"),
    ("glm", "glm-5", "0.60", "2.20"),
    ("mistral", "mistral-large-latest", "2.00", "6.00"),
    ("mistral", "mistral-medium-latest", "0.40", "2.00"),
    ("gemini", "gemini-embedding-001", "0.15", "0.00"),
    ("openai", "text-embedding-3-small", "0.02", "0.00"),
    ("mistral", "mistral-embed", "0.10", "0.00"),
    ("qwen", "text-embedding-v4", "0.07", "0.00"),
    ("glm", "embedding-3", "0.07", "0.00"),
)


def upgrade() -> None:
    for provider, model_id, input_cost, output_cost in COSTS:
        op.execute(
            "UPDATE ai_models SET input_price_per_mtok = "
            f"COALESCE(input_price_per_mtok, {input_cost}), output_price_per_mtok = "
            f"COALESCE(output_price_per_mtok, {output_cost}) "
            f"WHERE provider = '{provider}' AND model_id = '{model_id}'"
        )


def downgrade() -> None:
    for provider, model_id, input_cost, output_cost in COSTS:
        op.execute(
            "UPDATE ai_models SET input_price_per_mtok = NULL, output_price_per_mtok = NULL "
            f"WHERE provider = '{provider}' AND model_id = '{model_id}' "
            f"AND input_price_per_mtok = {input_cost} AND output_price_per_mtok = {output_cost}"
        )
