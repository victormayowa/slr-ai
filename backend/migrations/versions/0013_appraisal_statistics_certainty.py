"""Risk of bias assessments, reporting checklists, statistical analyses, IPD, GRADE, EtD, and interpretation

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-15 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
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


def _time(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _text(name: str) -> sa.Column:
    return sa.Column(name, sa.Text(), nullable=False, server_default="")


def _table(name: str, *columns: sa.Column | sa.Constraint, index: tuple[str, ...] = ()) -> None:
    op.create_table(name, _id(), *columns)
    for column in index:
        op.create_index(f"ix_{name}_{column}", name, [column])


TABLES = [
    "interpretation_texts",
    "prior_reviews",
    "etd_frameworks",
    "grade_assessments",
    "ipd_datasets",
    "analysis_runs",
    "analyses",
    "reporting_items",
    "reporting_assessments",
    "appraisal_ai_suggestions",
    "appraisal_domain_judgments",
    "appraisal_answers",
    "appraisal_assessments",
]


def upgrade() -> None:
    _table(
        "appraisal_assessments",
        _fk("project_id", "projects.id", "CASCADE"),
        _fk("study_id", "studies.id", "CASCADE"),
        sa.Column("tool", sa.String(40), nullable=False),
        sa.Column("tool_version", sa.String(120), nullable=False),
        sa.Column("outcome", sa.String(300), nullable=False, server_default=""),
        _text("result_description"),
        _text("selection_reason"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("overall_judgment", sa.String(30), nullable=True),
        _text("overall_rationale"),
        _user("signed_off_by_id"),
        _time("signed_off_at", nullable=True),
        _user("created_by_id"),
        _time("created_at"),
        _time("updated_at"),
        sa.UniqueConstraint("study_id", "tool", "outcome", name="uq_appraisal_assessment"),
        index=("project_id", "study_id"),
    )
    _table(
        "appraisal_answers",
        _fk("assessment_id", "appraisal_assessments.id", "CASCADE"),
        sa.Column("question_id", sa.String(20), nullable=False),
        sa.Column("answer", sa.String(30), nullable=False),
        _text("note"),
        sa.Column("span_ids", JSONB, nullable=False, server_default=EMPTY_LIST),
        sa.Column("source", sa.String(20), nullable=False),
        _user("answered_by_id"),
        _time("updated_at"),
        sa.UniqueConstraint("assessment_id", "question_id", name="uq_appraisal_answer"),
        index=("assessment_id",),
    )
    _table(
        "appraisal_domain_judgments",
        _fk("assessment_id", "appraisal_assessments.id", "CASCADE"),
        sa.Column("domain", sa.String(40), nullable=False),
        sa.Column("judgment", sa.String(30), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("algorithm_judgment", sa.String(30), nullable=True),
        _user("signed_off_by_id"),
        _time("signed_off_at"),
        sa.UniqueConstraint("assessment_id", "domain", name="uq_appraisal_domain"),
        index=("assessment_id",),
    )
    _table(
        "appraisal_ai_suggestions",
        _fk("ai_run_id", "ai_runs.id", "CASCADE"),
        _fk("assessment_id", "appraisal_assessments.id", "CASCADE"),
        sa.Column("question_id", sa.String(20), nullable=False, server_default=""),
        sa.Column("domain", sa.String(40), nullable=False, server_default=""),
        sa.Column("answer", sa.String(30), nullable=False),
        _text("rationale"),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("span_ids", JSONB, nullable=False, server_default=EMPTY_LIST),
        sa.Column("grounded", sa.Boolean(), nullable=False, server_default=sa.false()),
        index=("ai_run_id", "assessment_id"),
    )
    _table(
        "reporting_assessments",
        _fk("project_id", "projects.id", "CASCADE"),
        _fk("study_id", "studies.id", "CASCADE"),
        sa.Column("checklist", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        _user("signed_off_by_id"),
        _time("signed_off_at", nullable=True),
        _user("created_by_id"),
        _time("created_at"),
        _time("updated_at"),
        sa.UniqueConstraint("study_id", "checklist", name="uq_reporting_assessment"),
        index=("project_id", "study_id"),
    )
    _table(
        "reporting_items",
        _fk("assessment_id", "reporting_assessments.id", "CASCADE"),
        sa.Column("item_id", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=""),
        _text("location"),
        _text("note"),
        sa.Column("span_ids", JSONB, nullable=False, server_default=EMPTY_LIST),
        sa.Column("ai_status", sa.String(20), nullable=False, server_default=""),
        _text("ai_rationale"),
        sa.Column("ai_quote", sa.Text(), nullable=True),
        sa.Column("ai_grounded", sa.Boolean(), nullable=True),
        _fk("ai_run_id", "ai_runs.id", "SET NULL", nullable=True),
        _user("updated_by_id"),
        _time("updated_at"),
        sa.UniqueConstraint("assessment_id", "item_id", name="uq_reporting_item"),
        index=("assessment_id",),
    )
    _table(
        "analyses",
        _fk("project_id", "projects.id", "CASCADE"),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("outcome", sa.String(300), nullable=False),
        sa.Column("analysis_type", sa.String(20), nullable=False),
        sa.Column("spec", JSONB, nullable=False),
        sa.Column("prespecified", sa.Boolean(), nullable=False),
        sa.Column("plan_reference", sa.String(300), nullable=False, server_default=""),
        _text("justification"),
        sa.Column("status", sa.String(20), nullable=False),
        _user("approved_by_id"),
        _time("approved_at", nullable=True),
        _text("approval_note"),
        _user("created_by_id"),
        _time("created_at"),
        _time("updated_at"),
        index=("project_id",),
    )
    _table(
        "analysis_runs",
        _fk("analysis_id", "analyses.id", "CASCADE"),
        _fk("project_id", "projects.id", "CASCADE"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("spec", JSONB, nullable=False),
        sa.Column("spec_sha256", sa.String(64), nullable=False),
        sa.Column("dataset", JSONB, nullable=False),
        sa.Column("dataset_sha256", sa.String(64), nullable=False),
        _fk("extraction_snapshot_id", "stage_snapshots.id", "SET NULL", nullable=True),
        sa.Column("seed", sa.Integer(), nullable=False),
        _text("script"),
        sa.Column("results", JSONB, nullable=True),
        sa.Column("plots", JSONB, nullable=False, server_default=EMPTY_LIST),
        _text("session_info"),
        sa.Column("r_version", sa.String(60), nullable=False, server_default=""),
        sa.Column("packages", JSONB, nullable=False, server_default=EMPTY_OBJECT),
        sa.Column("error", sa.Text(), nullable=True),
        _text("log"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("is_final", sa.Boolean(), nullable=False, server_default=sa.false()),
        _user("started_by_id"),
        _time("created_at"),
        _time("finished_at", nullable=True),
        index=("analysis_id", "project_id"),
    )
    _table(
        "ipd_datasets",
        _fk("project_id", "projects.id", "CASCADE"),
        _fk("study_id", "studies.id", "CASCADE"),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("columns", JSONB, nullable=False),
        sa.Column("mapping", JSONB, nullable=False, server_default=EMPTY_OBJECT),
        sa.Column("validation", JSONB, nullable=False, server_default=EMPTY_OBJECT),
        _user("uploaded_by_id"),
        _time("created_at"),
        index=("project_id", "study_id"),
    )
    _table(
        "grade_assessments",
        _fk("project_id", "projects.id", "CASCADE"),
        sa.Column("outcome", sa.String(300), nullable=False),
        sa.Column("comparison", sa.String(300), nullable=False, server_default=""),
        _fk("analysis_id", "analyses.id", "SET NULL", nullable=True),
        sa.Column("importance", sa.String(20), nullable=False),
        sa.Column("starting_certainty", sa.String(20), nullable=False),
        sa.Column("domains", JSONB, nullable=False, server_default=EMPTY_OBJECT),
        sa.Column("certainty", sa.String(20), nullable=False),
        sa.Column("mid", sa.Float(), nullable=True),
        sa.Column("mid_scale", sa.String(20), nullable=False, server_default=""),
        sa.Column("outcome_direction", sa.String(20), nullable=False),
        sa.Column("baseline_risks", JSONB, nullable=False, server_default=EMPTY_LIST),
        sa.Column("status", sa.String(20), nullable=False),
        _user("signed_off_by_id"),
        _time("signed_off_at", nullable=True),
        _text("sign_off_note"),
        _user("created_by_id"),
        _time("created_at"),
        _time("updated_at"),
        sa.UniqueConstraint("project_id", "outcome", "comparison", name="uq_grade_assessment"),
        index=("project_id",),
    )
    _table(
        "etd_frameworks",
        _fk("project_id", "projects.id", "CASCADE"),
        sa.Column("title", sa.String(300), nullable=False),
        _text("question"),
        sa.Column("perspective", sa.String(30), nullable=False),
        sa.Column("criteria", JSONB, nullable=False, server_default=EMPTY_OBJECT),
        sa.Column("conclusions", JSONB, nullable=False, server_default=EMPTY_OBJECT),
        sa.Column("grade_assessment_ids", JSONB, nullable=False, server_default=EMPTY_LIST),
        sa.Column("status", sa.String(20), nullable=False),
        _user("signed_off_by_id"),
        _time("signed_off_at", nullable=True),
        _user("created_by_id"),
        _time("created_at"),
        _time("updated_at"),
        index=("project_id",),
    )
    _table(
        "prior_reviews",
        _fk("project_id", "projects.id", "CASCADE"),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("doi", sa.String(255), nullable=False, server_default=""),
        sa.Column("year", sa.String(20), nullable=False, server_default=""),
        sa.Column("openalex_id", sa.String(40), nullable=False, server_default=""),
        sa.Column("referenced_works", JSONB, nullable=False, server_default=EMPTY_LIST),
        sa.Column("outcome", sa.String(300), nullable=False, server_default=""),
        _text("conclusion"),
        sa.Column("conclusion_direction", sa.String(30), nullable=False, server_default=""),
        _user("created_by_id"),
        _time("created_at"),
        index=("project_id",),
    )
    _table(
        "interpretation_texts",
        _fk("project_id", "projects.id", "CASCADE"),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("outcome", sa.String(300), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("generated_by", sa.String(20), nullable=False),
        _fk("ai_run_id", "ai_runs.id", "SET NULL", nullable=True),
        sa.Column("unverified_numbers", JSONB, nullable=False, server_default=EMPTY_LIST),
        sa.Column("status", sa.String(20), nullable=False),
        _user("approved_by_id"),
        _time("approved_at", nullable=True),
        _user("created_by_id"),
        _time("created_at"),
        _time("updated_at"),
        index=("project_id",),
    )


def downgrade() -> None:
    for name in TABLES:
        op.drop_table(name)
