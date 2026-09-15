"""Dual and prioritized screening, full-text screening, studies, entity linking, and structured extraction

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-15 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
EMPTY_LIST = sa.text("'[]'::jsonb")
EMPTY_OBJECT = sa.text("'{}'::jsonb")


def _id() -> sa.Column:
    return sa.Column("id", sa.Integer(), primary_key=True)


def _fk(name: str, target: str, ondelete: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.Integer(), sa.ForeignKey(target, ondelete=ondelete), nullable=nullable)


def _user(name: str) -> sa.Column:
    return sa.Column(name, sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


def _created() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)


def _project_table(name: str, *columns: sa.Column | sa.Constraint) -> None:
    op.create_table(name, _id(), _fk("project_id", "projects.id", "CASCADE"), *columns)
    op.create_index(f"ix_{name}_project_id", name, ["project_id"])


def upgrade() -> None:
    op.alter_column(
        "screening_decisions", "decision", type_=sa.String(20), existing_type=sa.String(10), existing_nullable=False
    )
    op.add_column("screening_decisions", sa.Column("reason_code", sa.String(60), nullable=True))
    op.add_column("screening_decisions", sa.Column("note", sa.Text(), nullable=True))

    _project_table(
        "screening_adjudications",
        _fk("record_id", "records.id", "CASCADE"),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("reason_code", sa.String(60), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        _user("adjudicator_id"),
        _created(),
        sa.UniqueConstraint("record_id", "stage", name="uq_screening_adjudication"),
    )
    op.create_table(
        "review_settings",
        _id(),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), unique=True),
        sa.Column("screening", JSONB, nullable=False, server_default=EMPTY_OBJECT),
        sa.Column("extraction", JSONB, nullable=False, server_default=EMPTY_OBJECT),
        _user("updated_by_id"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _project_table(
        "screening_model_runs",
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("algorithm", sa.String(40), nullable=False),
        sa.Column("includes", sa.Integer(), nullable=False),
        sa.Column("excludes", sa.Integer(), nullable=False),
        sa.Column("decisions_count", sa.Integer(), nullable=False),
        sa.Column("ranked", sa.Integer(), nullable=False),
        sa.Column("top_terms", JSONB, nullable=False),
        _user("created_by_id"),
        _created(),
    )
    op.create_table(
        "screening_ranks",
        _id(),
        _fk("model_run_id", "screening_model_runs.id", "CASCADE"),
        _fk("record_id", "records.id", "CASCADE"),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
    )
    op.create_index("ix_screening_ranks_model_run_id", "screening_ranks", ["model_run_id"])
    _project_table(
        "stopping_evaluations",
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("method", sa.String(30), nullable=False),
        sa.Column("result", JSONB, nullable=False),
        sa.Column("records_total", sa.Integer(), nullable=False),
        sa.Column("decisions_count", sa.Integer(), nullable=False),
        _user("created_by_id"),
        _created(),
        _user("accepted_by_id"),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acceptance_rationale", sa.Text(), nullable=True),
    )
    _project_table(
        "qa_samples",
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("record_ids", JSONB, nullable=False),
        sa.Column("pool_size", sa.Integer(), nullable=False),
        _user("created_by_id"),
        _created(),
    )
    _project_table(
        "studies",
        sa.Column("label", sa.String(300), nullable=False),
        sa.Column("registry_ids", JSONB, nullable=False, server_default=EMPTY_LIST),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        _user("created_by_id"),
        _created(),
    )
    op.create_table(
        "study_reports",
        _id(),
        _fk("study_id", "studies.id", "CASCADE"),
        sa.Column("record_id", sa.Integer(), sa.ForeignKey("records.id", ondelete="CASCADE"), unique=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        _user("linked_by_id"),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_study_reports_study_id", "study_reports", ["study_id"])
    _project_table(
        "study_link_decisions",
        _fk("record_id", "records.id", "CASCADE"),
        _fk("other_record_id", "records.id", "CASCADE"),
        sa.Column("decision", sa.String(20), nullable=False),
        _user("decided_by_id"),
        _created(),
        sa.UniqueConstraint("record_id", "other_record_id", name="uq_study_link_decision"),
    )
    op.add_column("ai_runs", _fk("study_id", "studies.id", "SET NULL", nullable=True))

    op.add_column(
        "screening_suggestions", sa.Column("stage", sa.String(20), nullable=False, server_default="title_abstract")
    )
    op.add_column("screening_suggestions", sa.Column("confidence", sa.Float(), nullable=True))
    op.add_column(
        "screening_suggestions", sa.Column("criteria_judgments", JSONB, nullable=False, server_default=EMPTY_LIST)
    )
    op.add_column("screening_suggestions", _fk("document_id", "documents.id", "SET NULL", nullable=True))
    op.add_column("screening_suggestions", _fk("supporting_span_id", "document_spans.id", "SET NULL", nullable=True))

    _project_table(
        "document_entities",
        _fk("document_id", "documents.id", "CASCADE"),
        _fk("span_id", "document_spans.id", "CASCADE", nullable=True),
        sa.Column("entity_type", sa.String(30), nullable=False),
        sa.Column("text", sa.String(500), nullable=False),
        sa.Column("ontology", sa.String(20), nullable=False, server_default=""),
        sa.Column("code", sa.String(80), nullable=False, server_default=""),
        sa.Column("label", sa.String(500), nullable=False, server_default=""),
        sa.Column("candidates", JSONB, nullable=False, server_default=EMPTY_LIST),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        _fk("ai_run_id", "ai_runs.id", "SET NULL", nullable=True),
        _user("reviewed_by_id"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        _created(),
    )
    op.create_index("ix_document_entities_document_id", "document_entities", ["document_id"])

    for column in (
        sa.Column("section", sa.String(100), nullable=False, server_default="General"),
        sa.Column("field_type", sa.String(30), nullable=False, server_default="text"),
        sa.Column("options", JSONB, nullable=False, server_default=EMPTY_LIST),
        sa.Column("unit", sa.String(40), nullable=False, server_default=""),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("help_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("per_arm", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("outcome", sa.String(200), nullable=False, server_default=""),
        sa.Column("timepoint", sa.String(100), nullable=False, server_default=""),
        sa.Column("settings", JSONB, nullable=False, server_default=EMPTY_OBJECT),
    ):
        op.add_column("extraction_fields", column)

    op.add_column("extraction_suggestions", _fk("study_id", "studies.id", "CASCADE", nullable=True))
    op.create_index("ix_extraction_suggestions_study_id", "extraction_suggestions", ["study_id"])
    for column in (
        sa.Column("arm_label", sa.String(200), nullable=False, server_default=""),
        sa.Column("structured", JSONB, nullable=True),
        sa.Column("unit", sa.String(40), nullable=False, server_default=""),
        sa.Column("not_reported", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("span_ids", JSONB, nullable=False, server_default=EMPTY_LIST),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("ambiguous", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("grounding", sa.String(20), nullable=False, server_default=""),
    ):
        op.add_column("extraction_suggestions", column)

    op.create_table(
        "study_arms",
        _id(),
        _fk("study_id", "studies.id", "CASCADE"),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint("study_id", "label", name="uq_study_arm_label"),
    )
    op.create_index("ix_study_arms_study_id", "study_arms", ["study_id"])

    value_columns: tuple[sa.Column, ...] = (
        sa.Column("value", JSONB, nullable=True),
        sa.Column("not_reported", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("unit", sa.String(40), nullable=False, server_default=""),
        sa.Column("span_ids", JSONB, nullable=False, server_default=EMPTY_LIST),
    )
    _project_table(
        "extraction_values",
        _fk("study_id", "studies.id", "CASCADE"),
        _fk("field_id", "extraction_fields.id", "CASCADE"),
        _fk("arm_id", "study_arms.id", "CASCADE", nullable=True),
        _user("extractor_id"),
        *value_columns,
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("flags", JSONB, nullable=False, server_default=EMPTY_LIST),
        sa.Column("derivation", JSONB, nullable=True),
        sa.Column("source", sa.String(20), nullable=False),
        _fk("ai_suggestion_id", "extraction_suggestions.id", "SET NULL", nullable=True),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        _user("approved_by_id"),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        _created(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_extraction_values_study_id", "extraction_values", ["study_id"])
    op.create_index(
        "uq_extraction_value",
        "extraction_values",
        ["study_id", "field_id", "arm_id", "extractor_id"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    _project_table(
        "extraction_finals",
        _fk("study_id", "studies.id", "CASCADE"),
        _fk("field_id", "extraction_fields.id", "CASCADE"),
        _fk("arm_id", "study_arms.id", "CASCADE", nullable=True),
        sa.Column("value", JSONB, nullable=True),
        sa.Column("not_reported", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("unit", sa.String(40), nullable=False, server_default=""),
        sa.Column("span_ids", JSONB, nullable=False, server_default=EMPTY_LIST),
        sa.Column("flags", JSONB, nullable=False, server_default=EMPTY_LIST),
        sa.Column("derivation", JSONB, nullable=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        _user("decided_by_id"),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_extraction_finals_study_id", "extraction_finals", ["study_id"])
    op.create_index(
        "uq_extraction_final",
        "extraction_finals",
        ["study_id", "field_id", "arm_id"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    _project_table(
        "extraction_value_revisions",
        _fk("study_id", "studies.id", "CASCADE"),
        _fk("field_id", "extraction_fields.id", "CASCADE"),
        _fk("arm_id", "study_arms.id", "CASCADE", nullable=True),
        _user("extractor_id"),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("previous", JSONB, nullable=True),
        sa.Column("new", JSONB, nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        _user("changed_by_id"),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_extraction_value_revisions_study_id", "extraction_value_revisions", ["study_id"])
    _project_table(
        "author_contacts",
        _fk("study_id", "studies.id", "CASCADE"),
        sa.Column("contact_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("field_ids", JSONB, nullable=False, server_default=EMPTY_LIST),
        sa.Column("questions", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reminder_due", sa.String(10), nullable=True),
        sa.Column("response_summary", sa.Text(), nullable=False, server_default=""),
        _user("created_by_id"),
        _created(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_author_contacts_study_id", "author_contacts", ["study_id"])
    op.create_table(
        "author_contact_messages",
        _id(),
        _fk("contact_id", "author_contacts.id", "CASCADE"),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subject", sa.String(300), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False),
        _user("logged_by_id"),
        _created(),
    )
    op.create_index("ix_author_contact_messages_contact_id", "author_contact_messages", ["contact_id"])


def downgrade() -> None:
    op.drop_table("author_contact_messages")
    op.drop_table("author_contacts")
    op.drop_table("extraction_value_revisions")
    op.drop_table("extraction_finals")
    op.drop_table("extraction_values")
    op.drop_table("study_arms")
    op.drop_index("ix_extraction_suggestions_study_id", table_name="extraction_suggestions")
    for column in (
        "grounding",
        "ambiguous",
        "confidence",
        "span_ids",
        "not_reported",
        "unit",
        "structured",
        "arm_label",
        "study_id",
    ):
        op.drop_column("extraction_suggestions", column)
    for column in (
        "settings",
        "timepoint",
        "outcome",
        "per_arm",
        "help_text",
        "required",
        "unit",
        "options",
        "field_type",
        "section",
    ):
        op.drop_column("extraction_fields", column)
    op.drop_table("document_entities")
    for column in ("supporting_span_id", "document_id", "criteria_judgments", "confidence", "stage"):
        op.drop_column("screening_suggestions", column)
    op.drop_column("ai_runs", "study_id")
    op.drop_table("study_link_decisions")
    op.drop_table("study_reports")
    op.drop_table("studies")
    op.drop_table("qa_samples")
    op.drop_table("stopping_evaluations")
    op.drop_table("screening_ranks")
    op.drop_table("screening_model_runs")
    op.drop_table("review_settings")
    op.drop_table("screening_adjudications")
    op.drop_column("screening_decisions", "note")
    op.drop_column("screening_decisions", "reason_code")
    op.execute("DELETE FROM screening_decisions WHERE length(decision) > 10")
    op.alter_column(
        "screening_decisions", "decision", type_=sa.String(10), existing_type=sa.String(20), existing_nullable=False
    )
