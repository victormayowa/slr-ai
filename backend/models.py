from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from llm.providers import EMBEDDING_DIMENSIONS


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    first_name: Mapped[str] = mapped_column(String)
    last_name: Mapped[str] = mapped_column(String)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    institutional_email: Mapped[str | None] = mapped_column(String, unique=True, index=True)
    orcid_id: Mapped[str | None] = mapped_column(String, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String)
    position_role: Mapped[str | None] = mapped_column(String)
    reason_for_joining: Mapped[str | None] = mapped_column(String)
    institution: Mapped[str | None] = mapped_column(String)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    # Platform administrators manage the AI model catalog and benchmarks (scripts/make_admin.py).
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    email_notifications: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    organization_memberships: Mapped[list["OrganizationMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class UserAPIKey(Base):
    """A user's own key for an AI provider, encrypted with crypto.encrypt. Never returned to the browser."""

    __tablename__ = "user_api_keys"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_user_api_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # An llm.providers.PROVIDERS key.
    provider: Mapped[str] = mapped_column(String(20))
    encrypted_key: Mapped[bytes] = mapped_column(LargeBinary)
    last_four: Mapped[str] = mapped_column(String(4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIModel(Base):
    """A model projects can pin. Every AI run in a project uses its pinned model until someone changes it."""

    __tablename__ = "ai_models"
    __table_args__ = (
        UniqueConstraint("provider", "model_id", name="uq_ai_model"),
        Index("uq_ai_models_default_per_purpose", "purpose", unique=True, postgresql_where=text("is_default")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # An llm.providers.PROVIDERS key.
    provider: Mapped[str] = mapped_column(String(20))
    # The provider's model ID, sent with each request.
    model_id: Mapped[str] = mapped_column(String(100))
    label: Mapped[str] = mapped_column(String(200))
    # "chat" for text generation, or "embedding".
    purpose: Mapped[str] = mapped_column(String(10), default="chat", server_default="chat")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    # The model new projects start with for its purpose; at most one per purpose.
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # US dollars per million tokens. When unknown, run costs are left blank rather than estimated.
    input_price_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    output_price_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    # Benchmark validation (benchmarks.py): "unvalidated", "passed", "failed", or "exempt" (set by an administrator).
    benchmark_status: Mapped[str] = mapped_column(String(20), default="unvalidated", server_default="unvalidated")
    # Prompt versions (for example "screening-v3") the model passed benchmarks with.
    validated_prompts: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    status_note: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    members: Mapped[list["OrganizationMember"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class OrganizationMember(Base):
    __tablename__ = "organization_members"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_organization_member"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # "owner", "admin", or "member"
    role: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    organization: Mapped[Organization] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="organization_memberships")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL", name="fk_projects_organization_id")
    )
    # The user who created the project. Access is governed by ProjectMember roles, not this column.
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # The AI model every AI task in this project uses.
    ai_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_models.id", ondelete="SET NULL", name="fk_projects_ai_model_id")
    )
    # The model that embeds the project's records for similarity search.
    embedding_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_models.id", ondelete="SET NULL", name="fk_projects_embedding_model_id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    organization: Mapped[Organization | None] = relationship()
    ai_model: Mapped[AIModel | None] = relationship(foreign_keys=[ai_model_id])
    embedding_model: Mapped[AIModel | None] = relationship(foreign_keys=[embedding_model_id])
    members: Mapped[list["ProjectMember"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    protocol: Mapped["Protocol | None"] = relationship(back_populates="project", cascade="all, delete-orphan")
    extraction_fields: Mapped[list["ExtractionField"]] = relationship(
        order_by="ExtractionField.position", cascade="all, delete-orphan"
    )


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_member"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # A permissions.ProjectRole value.
    role: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    project: Mapped[Project] = relationship(back_populates="members")
    user: Mapped[User] = relationship()


class Protocol(Base):
    """Review setup for a project, its structured question, and its analysis plan. Versioned by protocol sign-off."""

    __tablename__ = "protocols"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), unique=True)
    review_type: Mapped[str] = mapped_column(String(50), default="Systematic Review")
    framework: Mapped[str] = mapped_column(String(20), default="PICO")
    description: Mapped[str] = mapped_column(Text, default="")
    suggested_criteria: Mapped[str] = mapped_column(Text, default="")
    extraction_outline: Mapped[str] = mapped_column(Text, default="")
    rob_tool: Mapped[str] = mapped_column(String(30), default="ROB-2")
    # The review question in one sentence, and the text of each element of the framework (protocol_frameworks).
    question: Mapped[str] = mapped_column(Text, default="", server_default="")
    question_elements: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # {criterion: {"rating": "yes" | "partly" | "no", "note": str}} for the FINER criteria.
    finer: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # Pre-specified outcomes, subgroup and sensitivity analyses, and synthesis approach (protocol_design_routes).
    analysis_plan: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    project: Mapped[Project] = relationship(back_populates="protocol")


class Criterion(Base):
    __tablename__ = "criteria"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    # "inclusion" or "exclusion"
    kind: Mapped[str] = mapped_column(String(10))
    text: Mapped[str] = mapped_column(Text)
    # "pending", "accepted", or "rejected"
    status: Mapped[str] = mapped_column(String(10), default="pending")
    # What the criterion restricts: a question element or a general element (protocol_frameworks).
    element: Mapped[str | None] = mapped_column(String(40))
    # "ai" when suggested by AI, "reviewer" when a reviewer wrote it.
    source: Mapped[str] = mapped_column(String(10), default="ai", server_default="ai")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SearchStrategy(Base):
    __tablename__ = "search_strategies"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    database: Mapped[str] = mapped_column(String(100))
    query: Mapped[str] = mapped_column(Text)
    # Increases with every change; each version is kept in search_strategy_versions.
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SearchRun(Base):
    """One execution of a database search or one file import; the provenance of every record."""

    __tablename__ = "search_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("search_strategies.id", ondelete="SET NULL"))
    strategy_version: Mapped[int | None] = mapped_column(Integer)
    # "database", "register", or "other" (PRISMA 2020 sources), or "import" for uploads with no source recorded
    kind: Mapped[str] = mapped_column(String(10))
    # The database searched, or the imported file's name.
    database: Mapped[str] = mapped_column(String(200))
    source_label: Mapped[str] = mapped_column(String(300))
    query: Mapped[str | None] = mapped_column(Text)
    result_count: Mapped[int] = mapped_column(Integer)
    # PRISMA-S details: the connector and interface used, how many results the source reported, when it was
    # searched, the export file format for imports, and any limits or filters applied.
    connector: Mapped[str | None] = mapped_column(String(40))
    interface: Mapped[str | None] = mapped_column(String(200))
    total_available: Mapped[int | None] = mapped_column(Integer)
    searched_on: Mapped[str | None] = mapped_column(String(40))
    file_format: Mapped[str | None] = mapped_column(String(20))
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    executed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    records: Mapped[list["Record"]] = relationship(
        back_populates="search_run", cascade="all, delete-orphan", passive_deletes=True
    )


class Record(Base):
    __tablename__ = "records"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    search_run_id: Mapped[int] = mapped_column(ForeignKey("search_runs.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(Text)
    authors: Mapped[str] = mapped_column(Text, default="")
    year: Mapped[str] = mapped_column(String(20), default="")
    venue: Mapped[str] = mapped_column(Text, default="")
    doi: Mapped[str] = mapped_column(String(255), default="", index=True)
    external_id: Mapped[str] = mapped_column(String(100), default="")
    abstract: Mapped[str] = mapped_column(Text, default="")
    # Other identifiers, such as {"pmid": ..., "pmcid": ..., "nct": ...}, used for deduplication.
    identifiers: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    url: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    # Set by deduplication; the record this one duplicates. Duplicates are kept for provenance but not screened.
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("records.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    search_run: Mapped[SearchRun] = relationship(back_populates="records")
    decisions: Mapped[list["ScreeningDecision"]] = relationship(
        back_populates="record", cascade="all, delete-orphan", order_by="ScreeningDecision.id", passive_deletes=True
    )
    ai_runs: Mapped[list["AIRun"]] = relationship(
        back_populates="record", cascade="all, delete-orphan", order_by="AIRun.id", passive_deletes=True
    )
    adjudications: Mapped[list["ScreeningAdjudication"]] = relationship(
        cascade="all, delete-orphan", order_by="ScreeningAdjudication.id", passive_deletes=True
    )


class AIRun(Base):
    """Provenance for one AI call: who triggered it, which provider, model, and prompt, and whether it failed."""

    __tablename__ = "ai_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    record_id: Mapped[int | None] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), index=True)
    # Set for work on a whole study (extraction); record_id is then the study's primary report.
    study_id: Mapped[int | None] = mapped_column(ForeignKey("studies.id", ondelete="SET NULL"))
    # "protocol", "screening", "fulltext_screening", "extraction", "appraisal", "entities", or "synthesis"
    task: Mapped[str] = mapped_column(String(20))
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(40))
    # "succeeded" or "failed"
    status: Mapped[str] = mapped_column(String(10))
    error: Mapped[str | None] = mapped_column(Text)
    # "user" when the triggering user's own API key paid for the call, "platform" for the server's key.
    key_source: Mapped[str | None] = mapped_column(String(10))
    # Summed over every attempt, including retries and repairs. Null when the provider didn't report it.
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    # Null when the model's prices aren't in the catalog.
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int | None] = mapped_column(Integer)
    triggered_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    record: Mapped[Record | None] = relationship(back_populates="ai_runs")
    screening: Mapped["ScreeningSuggestion | None"] = relationship(cascade="all, delete-orphan")
    extraction_values: Mapped[list["ExtractionSuggestion"]] = relationship(cascade="all, delete-orphan")
    appraisal: Mapped["AppraisalSuggestion | None"] = relationship(cascade="all, delete-orphan")


class ScreeningSuggestion(Base):
    """The AI's proposed eligibility decision. Never a final decision; see ScreeningDecision."""

    __tablename__ = "screening_suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    ai_run_id: Mapped[int] = mapped_column(ForeignKey("ai_runs.id", ondelete="CASCADE"), unique=True)
    # The stage suggested for: TITLE_ABSTRACT, or FULL_TEXT (read from document_id's passages).
    stage: Mapped[str] = mapped_column(String(20), default="title_abstract", server_default="title_abstract")
    # "Include", "Exclude", or "Maybe"
    decision: Mapped[str] = mapped_column(String(10))
    reasoning: Mapped[str] = mapped_column(Text, default="")
    supporting_quote: Mapped[str | None] = mapped_column(Text)
    # Whether supporting_quote appears in the record's text; null when there is no quote.
    quote_verified: Mapped[bool | None] = mapped_column(Boolean)
    # The model's confidence in its suggestion, from 0 to 1.
    confidence: Mapped[float | None] = mapped_column(Float)
    # [{"criterion_id", "kind", "text", "judgment": "met" | "not_met" | "unclear", "rationale", "quote",
    #   "quote_verified", "span_id"}] for each accepted criterion.
    criteria_judgments: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    supporting_span_id: Mapped[int | None] = mapped_column(ForeignKey("document_spans.id", ondelete="SET NULL"))


TITLE_ABSTRACT = "title_abstract"
FULL_TEXT = "full_text"


class ScreeningDecision(Base):
    """A reviewer's decision on a record. One per reviewer per stage; changing it replaces the row."""

    __tablename__ = "screening_decisions"
    __table_args__ = (UniqueConstraint("record_id", "stage", "reviewer_id", name="uq_screening_decision"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), index=True)
    # TITLE_ABSTRACT or FULL_TEXT
    stage: Mapped[str] = mapped_column(String(20))
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # "include", "exclude", or "undecided"; at full text also "not_retrieved" (the report couldn't be obtained)
    decision: Mapped[str] = mapped_column(String(20))
    # Why a report was excluded at full text (review_settings.exclusion_reasons).
    reason_code: Mapped[str | None] = mapped_column(String(60))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    record: Mapped[Record] = relationship(back_populates="decisions")
    reviewer: Mapped[User] = relationship()


class ScreeningAdjudication(Base):
    """The final decision on a record whose reviewers disagreed, made by a third reviewer with a rationale."""

    __tablename__ = "screening_adjudications"
    __table_args__ = (UniqueConstraint("record_id", "stage", name="uq_screening_adjudication"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"))
    stage: Mapped[str] = mapped_column(String(20))
    decision: Mapped[str] = mapped_column(String(20))
    reason_code: Mapped[str | None] = mapped_column(String(60))
    rationale: Mapped[str] = mapped_column(Text)
    adjudicator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    adjudicator: Mapped[User | None] = relationship()


class ExtractionField(Base):
    """One item of the extraction form. The value shape depends on field_type (extraction_values.FIELD_TYPES)."""

    __tablename__ = "extraction_fields"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_extraction_field_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    position: Mapped[int] = mapped_column(Integer, default=0)
    section: Mapped[str] = mapped_column(String(100), default="General", server_default="General")
    field_type: Mapped[str] = mapped_column(String(30), default="text", server_default="text")
    # Choices for categorical and multiple-choice fields.
    options: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # The unit values are normalized to, when set.
    unit: Mapped[str] = mapped_column(String(40), default="", server_default="")
    required: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    help_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Extracted once per study arm rather than once per study.
    per_arm: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # The analysis plan outcome and timepoint the field measures, if any.
    outcome: Mapped[str] = mapped_column(String(200), default="", server_default="")
    timepoint: Mapped[str] = mapped_column(String(100), default="", server_default="")
    # Extra options, such as {"analyte": "glucose", "ontology": "mesh", "direction": "lower_is_better"}.
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))


class ExtractionSuggestion(Base):
    """A value proposed by AI extraction. Never final: a reviewer accepts it into their own values."""

    __tablename__ = "extraction_suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    ai_run_id: Mapped[int] = mapped_column(ForeignKey("ai_runs.id", ondelete="CASCADE"), index=True)
    field_id: Mapped[int] = mapped_column(ForeignKey("extraction_fields.id", ondelete="CASCADE"))
    study_id: Mapped[int | None] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"), index=True)
    # The arm the value belongs to, as named in the report; empty for study-level fields.
    arm_label: Mapped[str] = mapped_column(String(200), default="", server_default="")
    value: Mapped[str] = mapped_column(Text)
    # The value in the field type's shape (extraction_values), when it could be read that way.
    structured: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    unit: Mapped[str] = mapped_column(String(40), default="", server_default="")
    not_reported: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # The passage the AI says the value comes from, and whether it appears in the record's text.
    # quote_verified is null when no value was reported.
    evidence_quote: Mapped[str | None] = mapped_column(Text)
    quote_verified: Mapped[bool | None] = mapped_column(Boolean)
    # Passages of the study's documents that hold the quote, found by the grounding check.
    span_ids: Mapped[list[int]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    confidence: Mapped[float | None] = mapped_column(Float)
    ambiguous: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # "grounded", "ungrounded" (the quote wasn't found, so the value can't be accepted), or "not_reported"
    grounding: Mapped[str] = mapped_column(String(20), default="", server_default="")

    field: Mapped[ExtractionField] = relationship()


class AppraisalSuggestion(Base):
    __tablename__ = "appraisal_suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    ai_run_id: Mapped[int] = mapped_column(ForeignKey("ai_runs.id", ondelete="CASCADE"), unique=True)
    tool: Mapped[str] = mapped_column(String(30))
    judgments: Mapped[dict[str, Any]] = mapped_column(JSONB)


class SynthesisReport(Base):
    __tablename__ = "synthesis_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    ai_run_id: Mapped[int] = mapped_column(ForeignKey("ai_runs.id", ondelete="CASCADE"))
    content: Mapped[str] = mapped_column(Text)
    record_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    ai_run: Mapped[AIRun] = relationship()


class AuditEvent(Base):
    """Append-only, hash-chained project history. Written only through audit.record_event."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(40))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))

    actor: Mapped[User | None] = relationship()


class ProjectStage(Base):
    """Sign-off state of one workflow stage. No row means the stage has never been completed."""

    __tablename__ = "project_stages"
    __table_args__ = (UniqueConstraint("project_id", "stage", name="uq_project_stage"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    # A workflow.STAGES value.
    stage: Mapped[str] = mapped_column(String(20))
    completed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completion_note: Mapped[str | None] = mapped_column(Text)
    reopened_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopen_rationale: Mapped[str | None] = mapped_column(Text)

    completed_by: Mapped[User | None] = relationship(foreign_keys=[completed_by_id])


class StageSnapshot(Base):
    """Hashed record of a stage's content at sign-off. Protocol snapshots are the protocol's versions."""

    __tablename__ = "stage_snapshots"
    __table_args__ = (UniqueConstraint("project_id", "stage", "version", name="uq_stage_snapshot_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(20))
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    sha256: Mapped[str] = mapped_column(String(64))
    note: Mapped[str] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    created_by: Mapped[User | None] = relationship()


class RecordEmbedding(Base):
    """A record's title and abstract embedded by one model. Vectors from different models are never compared."""

    __tablename__ = "record_embeddings"
    __table_args__ = (
        UniqueConstraint("record_id", "model", name="uq_record_embedding_model"),
        Index(
            "ix_record_embeddings_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"))
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    # "provider/model_id" of the embedding model.
    model: Mapped[str] = mapped_column(String(130))
    # SHA-256 of the embedded text, so unchanged records aren't embedded again.
    content_sha256: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AIJob(Base):
    """A batch of AI work run by a background worker (ai_tasks.run_job). Progress is saved as records finish."""

    __tablename__ = "ai_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    # "screening", "extraction", "appraisal", or "embedding"
    task: Mapped[str] = mapped_column(String(20))
    # "queued", "running", "completed", or "failed". A completed job can include records that failed.
    status: Mapped[str] = mapped_column(String(10))
    record_ids: Mapped[list[int]] = mapped_column(JSONB)
    total: Mapped[int] = mapped_column(Integer)
    processed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_by: Mapped[User | None] = relationship()


class ProtocolSuggestion(Base):
    """AI output for protocol design: a structured question, a section draft, or a consistency review.

    Never applied automatically; a reviewer saves what they accept.
    """

    __tablename__ = "protocol_suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    ai_run_id: Mapped[int] = mapped_column(ForeignKey("ai_runs.id", ondelete="CASCADE"), unique=True)
    # "question", "section", or "consistency"
    kind: Mapped[str] = mapped_column(String(20))
    section_key: Mapped[str | None] = mapped_column(String(40))
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    ai_run: Mapped[AIRun] = relationship()


class ProtocolSection(Base):
    """The text of one PRISMA-P section of the protocol document, as saved by a reviewer."""

    __tablename__ = "protocol_sections"
    __table_args__ = (UniqueConstraint("project_id", "key", name="uq_protocol_section"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    # A protocol_frameworks.PROTOCOL_SECTIONS key.
    key: Mapped[str] = mapped_column(String(40))
    content: Mapped[str] = mapped_column(Text)
    # Set when the text came from an accepted AI draft, and kept through later edits.
    based_on_suggestion_id: Mapped[int | None] = mapped_column(
        ForeignKey("protocol_suggestions.id", ondelete="SET NULL")
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    updated_by: Mapped[User | None] = relationship()


class TopicExploration(Base):
    """What's published and registered on a topic when a reviewer looked, used to judge novelty and feasibility."""

    __tablename__ = "topic_explorations"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    query: Mapped[str] = mapped_column(Text)
    # Workload estimate inputs (topic_exploration.WorkloadAssumptions).
    assumptions: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Counts, trends, existing reviews, registrations, and estimates (topic_exploration.explore_topic).
    results: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    created_by: Mapped[User | None] = relationship()


class ProtocolRegistration(Base):
    """Registration of the locked protocol (PROSPERO, OSF, or another registry), or a waiver with a reason."""

    __tablename__ = "protocol_registrations"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    # "PROSPERO", "OSF", another registry's name, or "none" for a waiver. (Named registry_name because SQLAlchemy
    # reserves `registry` on mapped classes.)
    registry_name: Mapped[str] = mapped_column("registry", String(40))
    # "deposited" (files on OSF, not yet registered), "submitted", "registered", or "waived"
    status: Mapped[str] = mapped_column(String(12))
    registration_id: Mapped[str] = mapped_column(String(100), default="")
    url: Mapped[str | None] = mapped_column(String(500))
    # The protocol version (stage snapshot) the registry record reflects.
    protocol_version: Mapped[int] = mapped_column(Integer)
    waiver_reason: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    created_by: Mapped[User | None] = relationship()


class DuplicateReview(Base):
    """A reviewer's decision on a pair of possible duplicates, so the pair isn't suggested again."""

    __tablename__ = "duplicate_reviews"
    __table_args__ = (UniqueConstraint("project_id", "record_id", "other_record_id", name="uq_duplicate_review"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    # The lower record id first.
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"))
    other_record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"))
    # "duplicate" or "not_duplicate"
    decision: Mapped[str] = mapped_column(String(20))
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SearchStrategyVersion(Base):
    """One version of a search strategy. Changes after the protocol is locked carry a note explaining them."""

    __tablename__ = "search_strategy_versions"
    __table_args__ = (UniqueConstraint("strategy_id", "version", name="uq_search_strategy_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("search_strategies.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    database: Mapped[str] = mapped_column(String(100))
    query: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    created_by: Mapped[User | None] = relationship()


class RecallCheck(Base):
    """Which known relevant articles (PMIDs or DOIs) a strategy version found."""

    __tablename__ = "recall_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("search_strategies.id", ondelete="CASCADE"))
    strategy_version: Mapped[int] = mapped_column(Integer)
    connector: Mapped[str] = mapped_column(String(40))
    seeds: Mapped[list[str]] = mapped_column(JSONB)
    found: Mapped[list[str]] = mapped_column(JSONB)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    created_by: Mapped[User | None] = relationship()


class PressReview(Base):
    """A PRESS 2015 peer review of one strategy version, or a project-wide waiver (no strategy)."""

    __tablename__ = "press_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("search_strategies.id", ondelete="CASCADE"))
    strategy_version: Mapped[int | None] = mapped_column(Integer)
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # "completed" or "waived"
    status: Mapped[str] = mapped_column(String(12))
    # {element: {"rating": ..., "comment": ...}} for the six PRESS elements.
    answers: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # "approved" or "revisions_required" for completed reviews.
    overall: Mapped[str | None] = mapped_column(String(20))
    # The reviewer's summary, or the waiver reason.
    comment: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    reviewer: Mapped[User | None] = relationship()


class CitationLink(Base):
    """A record found by citation searching, linked to the record whose references or citations it came from."""

    __tablename__ = "citation_links"
    __table_args__ = (UniqueConstraint("search_run_id", "seed_record_id", "record_id", name="uq_citation_link"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    search_run_id: Mapped[int] = mapped_column(ForeignKey("search_runs.id", ondelete="CASCADE"))
    seed_record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"))
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"))
    # "backward" (a reference of the seed) or "forward" (cites the seed)
    direction: Mapped[str] = mapped_column(String(10))


class Document(Base):
    """A full text or supplementary file for a record. The file is stored privately to the project (storage.py)."""

    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("record_id", "sha256", name="uq_document_record_file"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), index=True)
    # "full_text" or "supplement"
    role: Mapped[str] = mapped_column(String(20))
    # "europepmc", "unpaywall", or "upload"
    origin: Mapped[str] = mapped_column(String(20))
    source_url: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    file_name: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(255))
    # As reported by the source, such as "cc-by"; empty when unknown.
    license: Mapped[str] = mapped_column(String(100), default="", server_default="")
    # Unpaywall's open-access status ("gold", "green", "hybrid", "bronze") and copy version ("publishedVersion"...).
    oa_status: Mapped[str] = mapped_column(String(20), default="", server_default="")
    version: Mapped[str] = mapped_column(String(40), default="", server_default="")
    # "pending", "parsed", "failed" (for example a scanned PDF), or "unsupported" (stored but not read)
    parse_status: Mapped[str] = mapped_column(String(20))
    parse_error: Mapped[str | None] = mapped_column(Text)
    parser: Mapped[str] = mapped_column(String(60), default="", server_default="")
    page_count: Mapped[int | None] = mapped_column(Integer)
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    record: Mapped[Record] = relationship()
    uploaded_by: Mapped[User | None] = relationship()
    spans: Mapped[list["DocumentSpan"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentSpan.position", passive_deletes=True
    )


class DocumentSpan(Base):
    """A passage of a parsed document (heading, paragraph, table, caption, or reference) and where it appears.

    Offsets refer to the document's plain text: its span texts joined by document_parsing.SPAN_SEPARATOR.
    """

    __tablename__ = "document_spans"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))
    section: Mapped[str] = mapped_column(String(500), default="", server_default="")
    page: Mapped[int | None] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(100), default="", server_default="")
    text: Mapped[str] = mapped_column(Text)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)

    document: Mapped[Document] = relationship(back_populates="spans")


class FullTextRetrieval(Base):
    """One search for an open-access full text of a record, with what each source returned (PRISMA "reports sought")."""

    __tablename__ = "fulltext_retrievals"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), index=True)
    # "found", "already_stored", or "not_found"
    status: Mapped[str] = mapped_column(String(20))
    # [{"source": ..., "outcome": "found" | "not_found" | "skipped" | "error", "detail": ...}]
    attempts: Mapped[list[dict[str, str]]] = mapped_column(JSONB)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped[Document | None] = relationship()
    requested_by: Mapped[User | None] = relationship()


class ReviewSettings(Base):
    """How a project runs screening and extraction (review_settings.py). Absent means the defaults."""

    __tablename__ = "review_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), unique=True)
    screening: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    extraction: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ScreeningModelRun(Base):
    """One training of the prioritization model (active_learning.py) and the ranking it produced."""

    __tablename__ = "screening_model_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(20))
    algorithm: Mapped[str] = mapped_column(String(40))
    includes: Mapped[int] = mapped_column(Integer)
    excludes: Mapped[int] = mapped_column(Integer)
    # Human decisions in the project when the model was trained, to tell when it's due for retraining.
    decisions_count: Mapped[int] = mapped_column(Integer)
    ranked: Mapped[int] = mapped_column(Integer)
    top_terms: Mapped[list[str]] = mapped_column(JSONB, default=list)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScreeningRank(Base):
    __tablename__ = "screening_ranks"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_run_id: Mapped[int] = mapped_column(ForeignKey("screening_model_runs.id", ondelete="CASCADE"), index=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"))
    score: Mapped[float] = mapped_column(Float)
    rank: Mapped[int] = mapped_column(Integer)


class StoppingEvaluation(Base):
    """A stopping-rule test (stopping.py). Accepting it is a signed decision that screening may stop early."""

    __tablename__ = "stopping_evaluations"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(20))
    method: Mapped[str] = mapped_column(String(30))
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Unique records and human decisions when evaluated; the evaluation no longer applies once either changes.
    records_total: Mapped[int] = mapped_column(Integer)
    decisions_count: Mapped[int] = mapped_column(Integer)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    accepted_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acceptance_rationale: Mapped[str | None] = mapped_column(Text)

    accepted_by: Mapped[User | None] = relationship(foreign_keys=[accepted_by_id])


class QASample(Base):
    """A random sample of records not screened by humans (for example after stopping), screened to estimate recall."""

    __tablename__ = "qa_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(20))
    record_ids: Mapped[list[int]] = mapped_column(JSONB)
    # Unscreened records the sample was drawn from.
    pool_size: Mapped[int] = mapped_column(Integer)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Study(Base):
    """A study, which may be described by several reports (records). Extraction is done per study."""

    __tablename__ = "studies"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(300))
    # Trial registration numbers, such as ["NCT01234567"].
    registry_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    reports: Mapped[list["StudyReport"]] = relationship(
        back_populates="study", cascade="all, delete-orphan", order_by="StudyReport.id", passive_deletes=True
    )
    arms: Mapped[list["StudyArm"]] = relationship(
        cascade="all, delete-orphan", order_by="StudyArm.position", passive_deletes=True
    )


class StudyReport(Base):
    __tablename__ = "study_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"), index=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), unique=True)
    # The main report of the study, used to label it and read first.
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    linked_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    study: Mapped[Study] = relationship(back_populates="reports")
    record: Mapped[Record] = relationship()


class StudyLinkDecision(Base):
    """A reviewer's decision that two reports describe different studies, so the pair isn't suggested again."""

    __tablename__ = "study_link_decisions"
    __table_args__ = (UniqueConstraint("record_id", "other_record_id", name="uq_study_link_decision"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"))
    other_record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"))
    # "different_studies"
    decision: Mapped[str] = mapped_column(String(20))
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DocumentEntity(Base):
    """A mention in a document (condition, intervention, outcome...) linked to a terminology code once confirmed."""

    __tablename__ = "document_entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    span_id: Mapped[int | None] = mapped_column(ForeignKey("document_spans.id", ondelete="CASCADE"))
    # entity_linking.ENTITY_TYPES
    entity_type: Mapped[str] = mapped_column(String(30))
    # "mesh", "rxnorm", "atc", or "icd11", with the chosen code and its preferred label.
    ontology: Mapped[str] = mapped_column(String(20), default="", server_default="")
    code: Mapped[str] = mapped_column(String(80), default="", server_default="")
    label: Mapped[str] = mapped_column(String(500), default="", server_default="")
    # Codes found by terminology lookup: [{"ontology", "code", "label"}].
    candidates: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    text: Mapped[str] = mapped_column(String(500))
    # "suggested", "confirmed", or "rejected"
    status: Mapped[str] = mapped_column(String(20))
    # "ai" or "reviewer"
    source: Mapped[str] = mapped_column(String(20))
    ai_run_id: Mapped[int | None] = mapped_column(ForeignKey("ai_runs.id", ondelete="SET NULL"))
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StudyArm(Base):
    __tablename__ = "study_arms"
    __table_args__ = (UniqueConstraint("study_id", "label", name="uq_study_arm_label"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    position: Mapped[int] = mapped_column(Integer, default=0)


class ExtractionValue(Base):
    """One extractor's value for a field of a study (and arm). Dual extraction keeps each extractor's values apart."""

    __tablename__ = "extraction_values"
    __table_args__ = (
        Index(
            "uq_extraction_value",
            "study_id",
            "field_id",
            "arm_id",
            "extractor_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"), index=True)
    field_id: Mapped[int] = mapped_column(ForeignKey("extraction_fields.id", ondelete="CASCADE"))
    arm_id: Mapped[int | None] = mapped_column(ForeignKey("study_arms.id", ondelete="CASCADE"))
    extractor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # In the field type's shape (extraction_values); null when not reported.
    value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    not_reported: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    unit: Mapped[str] = mapped_column(String(40), default="", server_default="")
    # Evidence: passages of the study's documents and the quoted text.
    span_ids: Mapped[list[int]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    quote: Mapped[str | None] = mapped_column(Text)
    # Such as "calculated", "imputed", "unit_converted", or "ambiguous".
    flags: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # How a calculated, converted, or imputed value was derived: method, inputs, formula, reference.
    derivation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # "manual", "ai_accepted", "calculated", or "imputed"
    source: Mapped[str] = mapped_column(String(20))
    ai_suggestion_id: Mapped[int | None] = mapped_column(ForeignKey("extraction_suggestions.id", ondelete="SET NULL"))
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Imputed values need approval from someone other than the extractor before the dataset can be locked.
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    extractor: Mapped[User | None] = relationship(foreign_keys=[extractor_id])


class ExtractionFinal(Base):
    """The dataset's value: agreed by extractors, reconciled by a reviewer, or from single extraction."""

    __tablename__ = "extraction_finals"
    __table_args__ = (
        Index("uq_extraction_final", "study_id", "field_id", "arm_id", unique=True, postgresql_nulls_not_distinct=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"), index=True)
    field_id: Mapped[int] = mapped_column(ForeignKey("extraction_fields.id", ondelete="CASCADE"))
    arm_id: Mapped[int | None] = mapped_column(ForeignKey("study_arms.id", ondelete="CASCADE"))
    value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    not_reported: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    unit: Mapped[str] = mapped_column(String(40), default="", server_default="")
    span_ids: Mapped[list[int]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    flags: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    derivation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # "single", "agreed", or "reconciled"
    source: Mapped[str] = mapped_column(String(20))
    rationale: Mapped[str] = mapped_column(Text, default="", server_default="")
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExtractionValueRevision(Base):
    """Cell-level history: every change to an extractor's value or a final value, with who changed it and why."""

    __tablename__ = "extraction_value_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"), index=True)
    field_id: Mapped[int] = mapped_column(ForeignKey("extraction_fields.id", ondelete="CASCADE"))
    arm_id: Mapped[int | None] = mapped_column(ForeignKey("study_arms.id", ondelete="CASCADE"))
    extractor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # "value" (an extractor's) or "final"
    kind: Mapped[str] = mapped_column(String(10))
    previous: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    new: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(Text, default="", server_default="")
    changed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    changed_by: Mapped[User | None] = relationship(foreign_keys=[changed_by_id])


class AuthorContact(Base):
    """A request to a study's authors for missing or unclear data, and its correspondence."""

    __tablename__ = "author_contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"), index=True)
    contact_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320))
    field_ids: Mapped[list[int]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    questions: Mapped[str] = mapped_column(Text)
    # "draft", "sent", "replied", "no_response", or "closed"
    status: Mapped[str] = mapped_column(String(20))
    # When to follow up (YYYY-MM-DD) if there's no reply.
    reminder_due: Mapped[str | None] = mapped_column(String(10))
    response_summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    messages: Mapped[list["AuthorContactMessage"]] = relationship(
        cascade="all, delete-orphan", order_by="AuthorContactMessage.occurred_at", passive_deletes=True
    )


class AuthorContactMessage(Base):
    __tablename__ = "author_contact_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("author_contacts.id", ondelete="CASCADE"), index=True)
    # "outgoing" or "incoming"
    direction: Mapped[str] = mapped_column(String(10))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    subject: Mapped[str] = mapped_column(String(300), default="", server_default="")
    body: Mapped[str] = mapped_column(Text)
    logged_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppraisalAssessment(Base):
    """A risk of bias or quality appraisal of one study with one tool (appraisal_tools.py); for RoB 2, ROBINS-I, and
    ROBINS-E, of one result. Domain and overall judgments are reviewers' and are signed off with rationales."""

    __tablename__ = "appraisal_assessments"
    __table_args__ = (UniqueConstraint("study_id", "tool", "outcome", name="uq_appraisal_assessment"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"), index=True)
    tool: Mapped[str] = mapped_column(String(40))
    tool_version: Mapped[str] = mapped_column(String(120))
    # The outcome (and result) assessed, for tools assessed per result; empty for study-level tools.
    outcome: Mapped[str] = mapped_column(String(300), default="", server_default="")
    result_description: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Why the tool suits the study, from the tool recommendation or the reviewer.
    selection_reason: Mapped[str] = mapped_column(Text, default="", server_default="")
    # "in_progress" or "signed_off"
    status: Mapped[str] = mapped_column(String(20), default="in_progress")
    overall_judgment: Mapped[str | None] = mapped_column(String(30))
    overall_rationale: Mapped[str] = mapped_column(Text, default="", server_default="")
    signed_off_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    signed_off_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    answers: Mapped[list["AppraisalAnswer"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)
    domains: Mapped[list["AppraisalDomainJudgment"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)
    signed_off_by: Mapped[User | None] = relationship(foreign_keys=[signed_off_by_id])


class AppraisalAnswer(Base):
    __tablename__ = "appraisal_answers"
    __table_args__ = (UniqueConstraint("assessment_id", "question_id", name="uq_appraisal_answer"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("appraisal_assessments.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[str] = mapped_column(String(20))
    answer: Mapped[str] = mapped_column(String(30))
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    span_ids: Mapped[list[int]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # "manual" or "ai_accepted"
    source: Mapped[str] = mapped_column(String(20), default="manual")
    answered_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AppraisalDomainJudgment(Base):
    """A reviewer's signed-off judgment of one domain, with its rationale."""

    __tablename__ = "appraisal_domain_judgments"
    __table_args__ = (UniqueConstraint("assessment_id", "domain", name="uq_appraisal_domain"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("appraisal_assessments.id", ondelete="CASCADE"), index=True)
    domain: Mapped[str] = mapped_column(String(40))
    judgment: Mapped[str] = mapped_column(String(30))
    rationale: Mapped[str] = mapped_column(Text)
    # The tool algorithm's suggestion when the judgment was made, to show where reviewers departed from it.
    algorithm_judgment: Mapped[str | None] = mapped_column(String(30))
    signed_off_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    signed_off_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    signed_off_by: Mapped[User | None] = relationship(foreign_keys=[signed_off_by_id])


class AppraisalAISuggestion(Base):
    """An AI-suggested answer to a signalling question (or a domain judgment), with its evidence. Never final."""

    __tablename__ = "appraisal_ai_suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    ai_run_id: Mapped[int] = mapped_column(ForeignKey("ai_runs.id", ondelete="CASCADE"), index=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("appraisal_assessments.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[str] = mapped_column(String(20), default="", server_default="")
    domain: Mapped[str] = mapped_column(String(40), default="", server_default="")
    answer: Mapped[str] = mapped_column(String(30))
    rationale: Mapped[str] = mapped_column(Text, default="", server_default="")
    quote: Mapped[str | None] = mapped_column(Text)
    span_ids: Mapped[list[int]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    grounded: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class ReportingAssessment(Base):
    """How completely a study reports the items of a reporting guideline (CONSORT, STROBE, STARD, TRIPOD)."""

    __tablename__ = "reporting_assessments"
    __table_args__ = (UniqueConstraint("study_id", "checklist", name="uq_reporting_assessment"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"), index=True)
    checklist: Mapped[str] = mapped_column(String(30))
    # "in_progress" or "signed_off"
    status: Mapped[str] = mapped_column(String(20), default="in_progress")
    signed_off_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    signed_off_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    items: Mapped[list["ReportingItem"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)


class ReportingItem(Base):
    __tablename__ = "reporting_items"
    __table_args__ = (UniqueConstraint("assessment_id", "item_id", name="uq_reporting_item"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("reporting_assessments.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[str] = mapped_column(String(10))
    # The reviewer's assessment: "reported", "partially_reported", "not_reported", "not_applicable", or "" (not yet)
    status: Mapped[str] = mapped_column(String(20), default="", server_default="")
    location: Mapped[str] = mapped_column(Text, default="", server_default="")
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    span_ids: Mapped[list[int]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # The AI's flag, kept apart from the reviewer's assessment.
    ai_status: Mapped[str] = mapped_column(String(20), default="", server_default="")
    ai_rationale: Mapped[str] = mapped_column(Text, default="", server_default="")
    ai_quote: Mapped[str | None] = mapped_column(Text)
    ai_grounded: Mapped[bool | None] = mapped_column(Boolean)
    ai_run_id: Mapped[int | None] = mapped_column(ForeignKey("ai_runs.id", ondelete="SET NULL"))
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Analysis(Base):
    """A planned statistical analysis (stats_engine.py): its specification, whether it was pre-specified, and the
    statistician's approval of the model choice. Changing an approved specification returns it to draft."""

    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    outcome: Mapped[str] = mapped_column(String(300))
    # stats_engine.ANALYSIS_TYPES
    analysis_type: Mapped[str] = mapped_column(String(20))
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    prespecified: Mapped[bool] = mapped_column(Boolean)
    # The analysis plan item it implements (outcome, subgroup, or sensitivity analysis name).
    plan_reference: Mapped[str] = mapped_column(String(300), default="", server_default="")
    justification: Mapped[str] = mapped_column(Text, default="", server_default="")
    # "draft" or "approved"
    status: Mapped[str] = mapped_column(String(20), default="draft")
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_note: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    runs: Mapped[list["AnalysisRun"]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan", order_by="AnalysisRun.id", passive_deletes=True
    )
    approved_by: Mapped[User | None] = relationship(foreign_keys=[approved_by_id])


class AnalysisRun(Base):
    """One execution of an analysis in R, with everything needed to reproduce it: the exact specification and data,
    the script, the seed, R and package versions, results, and plots."""

    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    # "queued", "running", "succeeded", or "failed"
    status: Mapped[str] = mapped_column(String(20))
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    spec_sha256: Mapped[str] = mapped_column(String(64))
    # The analysis data set (one row per study, arm, or comparison) as sent to R.
    dataset: Mapped[dict[str, Any]] = mapped_column(JSONB)
    dataset_sha256: Mapped[str] = mapped_column(String(64))
    # The locked extraction data set the rows came from.
    extraction_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("stage_snapshots.id", ondelete="SET NULL"))
    seed: Mapped[int] = mapped_column(Integer)
    script: Mapped[str] = mapped_column(Text, default="", server_default="")
    results: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # [{"name", "format", "storage_key", "media_type", "size_bytes"}]
    plots: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    session_info: Mapped[str] = mapped_column(Text, default="", server_default="")
    r_version: Mapped[str] = mapped_column(String(60), default="", server_default="")
    packages: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    error: Mapped[str | None] = mapped_column(Text)
    log: Mapped[str] = mapped_column(Text, default="", server_default="")
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    # Run from an approved specification and the locked data set, so its results can be reported.
    is_final: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    started_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    analysis: Mapped[Analysis] = relationship(back_populates="runs")


class IPDDataset(Base):
    """Individual participant data from one study, stored encrypted; every access is recorded in the audit trail."""

    __tablename__ = "ipd_datasets"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"), index=True)
    file_name: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    rows: Mapped[int] = mapped_column(Integer)
    columns: Mapped[list[str]] = mapped_column(JSONB)
    # Standard variables (stats_engine.IPD_VARIABLES) mapped to this file's columns, and their harmonized codes.
    mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    validation: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GradeAssessment(Base):
    """GRADE certainty of evidence for one outcome (and comparison), with each domain's rating and rationale, signed
    off by a methodologist."""

    __tablename__ = "grade_assessments"
    __table_args__ = (UniqueConstraint("project_id", "outcome", "comparison", name="uq_grade_assessment"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    outcome: Mapped[str] = mapped_column(String(300))
    comparison: Mapped[str] = mapped_column(String(300), default="", server_default="")
    analysis_id: Mapped[int | None] = mapped_column(ForeignKey("analyses.id", ondelete="SET NULL"))
    # "critical", "important", or "not_important"
    importance: Mapped[str] = mapped_column(String(20), default="critical")
    # "high" for randomized trials, "low" for observational studies
    starting_certainty: Mapped[str] = mapped_column(String(20), default="high")
    # {domain: {"rating": int, "rationale": str}} for grading.DOMAINS
    domains: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # "high", "moderate", "low", or "very_low"
    certainty: Mapped[str] = mapped_column(String(20), default="high")
    # The minimal important difference and its scale ("per_1000" for risk differences, "units" for mean differences).
    mid: Mapped[float | None] = mapped_column(Float)
    mid_scale: Mapped[str] = mapped_column(String(20), default="", server_default="")
    # "lower_is_better" (for example mortality) or "higher_is_better"
    outcome_direction: Mapped[str] = mapped_column(String(20), default="lower_is_better")
    # [{"label", "risk"}]: baseline (comparator) risks for absolute effects, from 0 to 1.
    baseline_risks: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # "draft" or "signed_off"
    status: Mapped[str] = mapped_column(String(20), default="draft")
    signed_off_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    signed_off_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sign_off_note: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    signed_off_by: Mapped[User | None] = relationship(foreign_keys=[signed_off_by_id])


class EtdFramework(Base):
    """A GRADE Evidence to Decision framework: judgments on each criterion and the resulting recommendation."""

    __tablename__ = "etd_frameworks"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    question: Mapped[str] = mapped_column(Text, default="", server_default="")
    # "clinical_population", "clinical_individual", or "health_system"
    perspective: Mapped[str] = mapped_column(String(30), default="clinical_population")
    # {criterion: {"judgment", "research_evidence", "additional_considerations"}} for grading.ETD_CRITERIA
    criteria: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    conclusions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    grade_assessment_ids: Mapped[list[int]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # "draft" or "signed_off"
    status: Mapped[str] = mapped_column(String(20), default="draft")
    signed_off_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    signed_off_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PriorReview(Base):
    """An earlier systematic review on the question, with the works it cites, to compare overlap and conclusions."""

    __tablename__ = "prior_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(Text)
    doi: Mapped[str] = mapped_column(String(255), default="", server_default="")
    year: Mapped[str] = mapped_column(String(20), default="", server_default="")
    openalex_id: Mapped[str] = mapped_column(String(40), default="", server_default="")
    # OpenAlex ids of the works the review cites.
    referenced_works: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    outcome: Mapped[str] = mapped_column(String(300), default="", server_default="")
    conclusion: Mapped[str] = mapped_column(Text, default="", server_default="")
    # "favours_intervention", "favours_comparator", "no_difference", or "uncertain"
    conclusion_direction: Mapped[str] = mapped_column(String(30), default="", server_default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InterpretationText(Base):
    """Interpretive text (informative statements, limitations, plain-language summary) awaiting or holding a clinical
    expert's approval. AI-written text records any numbers that don't appear in the summary of findings."""

    __tablename__ = "interpretation_texts"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    # "plain_language_summary", "limitations", or "informative_statement"
    kind: Mapped[str] = mapped_column(String(30))
    outcome: Mapped[str] = mapped_column(String(300), default="", server_default="")
    content: Mapped[str] = mapped_column(Text)
    # "rules", "ai", or "reviewer"
    generated_by: Mapped[str] = mapped_column(String(20))
    ai_run_id: Mapped[int | None] = mapped_column(ForeignKey("ai_runs.id", ondelete="SET NULL"))
    unverified_numbers: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # "draft" or "approved"
    status: Mapped[str] = mapped_column(String(20), default="draft")
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# --- Manuscript (W12) ---


class Manuscript(Base):
    """The review manuscript, drafted from the locked evidence base. Sections hold Markdown with evidence markers
    ([#analysis:3]), citations ([@5]), and table or figure embeds ([[table:sof]]), so every claim stays traceable."""

    __tablename__ = "manuscripts"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), unique=True)
    title: Mapped[str] = mapped_column(String(500))
    # "vancouver", "apa", "ama", "harvard", or a style id from the CSL styles repository (for example "nature")
    citation_style: Mapped[str] = mapped_column(String(100), default="vancouver")
    keywords: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # funding, competing_interests, data_availability, ethics, registration, acknowledgements
    statements: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # graphical_abstract, highlights, plain_language_summary
    extras: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # The certainty stage snapshot (the locked evidence base) the manuscript was started from.
    evidence_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("stage_snapshots.id", ondelete="SET NULL"))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    sections: Mapped[list["ManuscriptSection"]] = relationship(
        cascade="all, delete-orphan", order_by="ManuscriptSection.position", passive_deletes=True
    )
    authors: Mapped[list["ManuscriptAuthor"]] = relationship(
        cascade="all, delete-orphan", order_by="ManuscriptAuthor.position", passive_deletes=True
    )
    versions: Mapped[list["ManuscriptVersion"]] = relationship(
        cascade="all, delete-orphan", order_by="ManuscriptVersion.number", passive_deletes=True
    )


class ManuscriptSection(Base):
    __tablename__ = "manuscript_sections"
    __table_args__ = (UniqueConstraint("manuscript_id", "key", name="uq_manuscript_section"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    manuscript_id: Mapped[int] = mapped_column(ForeignKey("manuscripts.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    position: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text, default="", server_default="")
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ManuscriptRevision(Base):
    """Every saved change to a section, for tracked changes and diffs."""

    __tablename__ = "manuscript_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    section_id: Mapped[int] = mapped_column(ForeignKey("manuscript_sections.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text)
    # "manual", "generated", "ai_accepted", or "restored"
    source: Mapped[str] = mapped_column(String(20))
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    suggestion_id: Mapped[int | None] = mapped_column(Integer)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    created_by: Mapped[User | None] = relationship()


class ManuscriptSuggestion(Base):
    """A proposed replacement for a section (an AI draft, a language edit, or regenerated text), never applied until a
    reviewer accepts it. Its verification report is kept with it."""

    __tablename__ = "manuscript_suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    section_id: Mapped[int] = mapped_column(ForeignKey("manuscript_sections.id", ondelete="CASCADE"), index=True)
    # "draft", "generated", "academic_tone", "grammar", "journal_style", "plain_language"
    kind: Mapped[str] = mapped_column(String(30))
    instruction: Mapped[str] = mapped_column(Text, default="", server_default="")
    original: Mapped[str] = mapped_column(Text, default="", server_default="")
    content: Mapped[str] = mapped_column(Text)
    problems: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    ai_run_id: Mapped[int | None] = mapped_column(ForeignKey("ai_runs.id", ondelete="SET NULL"))
    # "pending", "accepted", or "rejected"
    status: Mapped[str] = mapped_column(String(20), default="pending")
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ClaimAcknowledgement(Base):
    """A reviewer's acknowledgement that a sentence needs no linked evidence, or that its numbers are right."""

    __tablename__ = "claim_acknowledgements"
    __table_args__ = (UniqueConstraint("section_id", "sentence_hash", name="uq_claim_acknowledgement"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    section_id: Mapped[int] = mapped_column(ForeignKey("manuscript_sections.id", ondelete="CASCADE"), index=True)
    sentence_hash: Mapped[str] = mapped_column(String(64))
    sentence: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(Text)
    acknowledged_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ManuscriptReference(Base):
    """A cited work, as CSL-JSON, with DOI and metadata verification and retraction checks."""

    __tablename__ = "manuscript_references"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    record_id: Mapped[int | None] = mapped_column(ForeignKey("records.id", ondelete="SET NULL"))
    csl: Mapped[dict[str, Any]] = mapped_column(JSONB)
    doi: Mapped[str] = mapped_column(String(255), default="", server_default="")
    pmid: Mapped[str] = mapped_column(String(20), default="", server_default="")
    # "unchecked", "verified", "mismatch", "not_found", or "error"
    verification_status: Mapped[str] = mapped_column(String(20), default="unchecked")
    verification: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    retracted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    retraction: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ManuscriptAuthor(Base):
    __tablename__ = "manuscript_authors"

    id: Mapped[int] = mapped_column(primary_key=True)
    manuscript_id: Mapped[int] = mapped_column(ForeignKey("manuscripts.id", ondelete="CASCADE"), index=True)
    # Authors who approve the manuscript in OmniReview have an account on the project.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(300))
    email: Mapped[str] = mapped_column(String(320), default="", server_default="")
    affiliation: Mapped[str] = mapped_column(Text, default="", server_default="")
    orcid: Mapped[str] = mapped_column(String(40), default="", server_default="")
    position: Mapped[int] = mapped_column(Integer)
    corresponding: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # CRediT contributor roles
    credit_roles: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    competing_interests: Mapped[str] = mapped_column(Text, default="", server_default="")


class ManuscriptVersion(Base):
    """A frozen copy of the manuscript that authors approve; export needs every author's approval of the current one."""

    __tablename__ = "manuscript_versions"
    __table_args__ = (UniqueConstraint("manuscript_id", "number", name="uq_manuscript_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    manuscript_id: Mapped[int] = mapped_column(ForeignKey("manuscripts.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    sha256: Mapped[str] = mapped_column(String(64))
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    approvals: Mapped[list["AuthorApproval"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)


class AuthorApproval(Base):
    __tablename__ = "author_approvals"
    __table_args__ = (UniqueConstraint("version_id", "author_id", name="uq_author_approval"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("manuscript_versions.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("manuscript_authors.id", ondelete="CASCADE"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ManuscriptChecklistItem(Base):
    """A reviewer's status and location for a reporting checklist item, overriding the automatic assessment."""

    __tablename__ = "manuscript_checklist_items"
    __table_args__ = (UniqueConstraint("manuscript_id", "checklist", "item_id", name="uq_manuscript_checklist_item"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    manuscript_id: Mapped[int] = mapped_column(ForeignKey("manuscripts.id", ondelete="CASCADE"), index=True)
    checklist: Mapped[str] = mapped_column(String(40))
    item_id: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="", server_default="")
    location: Mapped[str] = mapped_column(Text, default="", server_default="")
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# --- Publication (W13) ---


class JournalCandidate(Base):
    """A journal suggested for the manuscript, with open access, indexing, and metrics, and heuristic warnings."""

    __tablename__ = "journal_candidates"
    __table_args__ = (UniqueConstraint("project_id", "openalex_id", name="uq_journal_candidate"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    openalex_id: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(500))
    issns: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    publisher: Mapped[str] = mapped_column(String(500), default="", server_default="")
    homepage: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    is_oa: Mapped[bool | None] = mapped_column(Boolean)
    in_doaj: Mapped[bool | None] = mapped_column(Boolean)
    apc_usd: Mapped[int | None] = mapped_column(Integer)
    h_index: Mapped[int | None] = mapped_column(Integer)
    mean_citedness: Mapped[float | None] = mapped_column(Float)
    works_count: Mapped[int | None] = mapped_column(Integer)
    medline_indexed: Mapped[bool | None] = mapped_column(Boolean)
    topic_works: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    included_study_reports: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    score: Mapped[float] = mapped_column(Float, default=0)
    reasons: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    warnings: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    shortlisted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JournalGuideline(Base):
    """A journal's author guidelines and the structured requirements read from them, each backed by a quote."""

    __tablename__ = "journal_guidelines"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    journal_name: Mapped[str] = mapped_column(String(500))
    source_url: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    file_name: Mapped[str] = mapped_column(String(255), default="", server_default="")
    content: Mapped[str] = mapped_column(Text)
    requirements: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    ai_run_id: Mapped[int | None] = mapped_column(ForeignKey("ai_runs.id", ondelete="SET NULL"))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SubmissionPackage(Base):
    """Everything a journal submission needs, built from an approved manuscript version and checked for readiness.
    OmniReview never submits: the corresponding author confirms the package and submits it on the journal's system."""

    __tablename__ = "submission_packages"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    manuscript_version_id: Mapped[int | None] = mapped_column(ForeignKey("manuscript_versions.id", ondelete="SET NULL"))
    guideline_id: Mapped[int | None] = mapped_column(ForeignKey("journal_guidelines.id", ondelete="SET NULL"))
    journal_name: Mapped[str] = mapped_column(String(500), default="", server_default="")
    cover_letter: Mapped[str] = mapped_column(Text, default="", server_default="")
    cover_letter_ai_run_id: Mapped[int | None] = mapped_column(ForeignKey("ai_runs.id", ondelete="SET NULL"))
    highlights: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    storage_key: Mapped[str] = mapped_column(String(255), default="", server_default="")
    sha256: Mapped[str] = mapped_column(String(64), default="", server_default="")
    files: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    readiness: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # "draft", "built", or "confirmed"
    status: Mapped[str] = mapped_column(String(20), default="draft")
    confirmed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RepositoryDeposit(Base):
    """Review materials deposited in a repository (Zenodo, OSF, Figshare, GitHub, GitLab) or packaged for one without an
    API (Dryad, medRxiv). Access tokens are used per request and never stored."""

    __tablename__ = "repository_deposits"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    target: Mapped[str] = mapped_column(String(20))
    # "draft", "published", "packaged", or "failed"
    status: Mapped[str] = mapped_column(String(20))
    sandbox: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    external_id: Mapped[str] = mapped_column(String(200), default="", server_default="")
    concept_id: Mapped[str] = mapped_column(String(100), default="", server_default="")
    doi: Mapped[str] = mapped_column(String(255), default="", server_default="")
    url: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    storage_key: Mapped[str] = mapped_column(String(255), default="", server_default="")
    files: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    deposit_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(Text)
    release_id: Mapped[int | None] = mapped_column(ForeignKey("review_releases.id", ondelete="SET NULL"))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewRound(Base):
    """One round of peer review: the reviewers' comments, point-by-point responses, and the response letter."""

    __tablename__ = "review_rounds"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    journal: Mapped[str] = mapped_column(String(500), default="", server_default="")
    round_number: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(40), default="", server_default="")
    received_on: Mapped[str] = mapped_column(String(20), default="", server_default="")
    # The manuscript version reviewed, to show what changed since.
    manuscript_version_id: Mapped[int | None] = mapped_column(ForeignKey("manuscript_versions.id", ondelete="SET NULL"))
    response_letter: Mapped[str] = mapped_column(Text, default="", server_default="")
    response_ai_run_id: Mapped[int | None] = mapped_column(ForeignKey("ai_runs.id", ondelete="SET NULL"))
    # "open" or "responded"
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    comments: Mapped[list["ReviewerComment"]] = relationship(
        cascade="all, delete-orphan", order_by="ReviewerComment.id", passive_deletes=True
    )


class ReviewerComment(Base):
    __tablename__ = "reviewer_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("review_rounds.id", ondelete="CASCADE"), index=True)
    reviewer: Mapped[str] = mapped_column(String(100))
    number: Mapped[str] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text)
    # "major", "minor", "editorial", "methods", "statistics", or "other"
    category: Mapped[str] = mapped_column(String(30), default="", server_default="")
    response: Mapped[str] = mapped_column(Text, default="", server_default="")
    # "open", "addressed", or "rebutted"
    status: Mapped[str] = mapped_column(String(20), default="open")
    # [{"section_key", "revision_id"}]: the manuscript changes made in response.
    changes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # {"stage", "rationale", "reopened_at"} when the comment led to re-analysis.
    reanalysis: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# --- Living reviews (W14) ---


class SurveillanceSchedule(Base):
    """A saved search strategy rerun on a schedule to find new studies."""

    __tablename__ = "surveillance_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("search_strategies.id", ondelete="CASCADE"))
    connector: Mapped[str] = mapped_column(String(40))
    frequency_days: Mapped[int] = mapped_column(Integer)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    # min_new_records, min_predicted_relevant, relevance_threshold, large_trial_participants
    thresholds: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    strategy: Mapped[SearchStrategy] = relationship()


class SurveillanceRun(Base):
    __tablename__ = "surveillance_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    schedule_id: Mapped[int | None] = mapped_column(
        ForeignKey("surveillance_schedules.id", ondelete="CASCADE"), index=True
    )
    # "search", "retractions", or "feeds"
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    query: Mapped[str] = mapped_column(Text, default="", server_default="")
    database: Mapped[str] = mapped_column(String(200), default="", server_default="")
    retrieved: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    new_candidates: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    duplicates: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SurveillanceCandidate(Base):
    """A record found by surveillance, kept apart from the review until a reviewer promotes it into a living update."""

    __tablename__ = "surveillance_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("surveillance_runs.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(Text)
    authors: Mapped[str] = mapped_column(Text, default="", server_default="")
    year: Mapped[str] = mapped_column(String(20), default="", server_default="")
    venue: Mapped[str] = mapped_column(Text, default="", server_default="")
    doi: Mapped[str] = mapped_column(String(255), default="", server_default="")
    abstract: Mapped[str] = mapped_column(Text, default="", server_default="")
    identifiers: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    url: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    external_id: Mapped[str] = mapped_column(String(100), default="", server_default="")
    relevance: Mapped[float | None] = mapped_column(Float)
    sample_size: Mapped[int | None] = mapped_column(Integer)
    ai_decision: Mapped[str | None] = mapped_column(String(20))
    ai_reasoning: Mapped[str | None] = mapped_column(Text)
    ai_run_id: Mapped[int | None] = mapped_column(ForeignKey("ai_runs.id", ondelete="SET NULL"))
    # "pending", "promoted", "dismissed", or "imported"
    status: Mapped[str] = mapped_column(String(20), default="pending")
    decision_reason: Mapped[str] = mapped_column(Text, default="", server_default="")
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    record_id: Mapped[int | None] = mapped_column(ForeignKey("records.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WatchFeed(Base):
    """A guideline, regulatory, or journal RSS or Atom feed watched for new items."""

    __tablename__ = "watch_feeds"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(String(1000))
    seen_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SurveillanceAlert(Base):
    __tablename__ = "surveillance_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    # "new_records", "new_eligible", "large_trial", "retraction", or "feed_update"
    kind: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(500))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # "open", "acknowledged", or "dismissed"
    status: Mapped[str] = mapped_column(String(20), default="open")
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    acknowledged_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ImpactAssessment(Base):
    """A provisional re-analysis with candidate studies added, showing how results and certainty might change before
    anyone commits to a living update. Never final."""

    __tablename__ = "impact_assessments"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"))
    candidate_rows: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    baseline: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    provisional: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    shift: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    grade_changes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewRelease(Base):
    """A versioned release of the review: hashes of every stage snapshot, the approved manuscript, and final analyses,
    with a changelog against the previous release."""

    __tablename__ = "review_releases"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_review_release"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    changelog: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    sha256: Mapped[str] = mapped_column(String(64))
    released_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --- Collaboration (W15) ---


class Invitation(Base):
    """An invitation to join a project with a role, accepted by signing in with the invited email address. Only the
    token's hash is stored."""

    __tablename__ = "invitations"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(254))
    role: Mapped[str] = mapped_column(String(30))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    project: Mapped[Project] = relationship()
    invited_by: Mapped[User | None] = relationship(foreign_keys=[invited_by_id])


class Task(Base):
    """A piece of review work with an assignee and deadline, optionally tied to a workflow stage or an item."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    stage: Mapped[str] = mapped_column(String(30), default="", server_default="")
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    due_on: Mapped[date | None] = mapped_column(Date)
    # "open", "in_progress", or "done"
    status: Mapped[str] = mapped_column(String(20), default="open")
    # "low", "normal", or "high"
    priority: Mapped[str] = mapped_column(String(10), default="normal")
    anchor_key: Mapped[str] = mapped_column(String(200), default="", server_default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    assignee: Mapped[User | None] = relationship(foreign_keys=[assignee_id])


class Comment(Base):
    """A discussion comment anchored to something in the project (a record, a passage, an extraction cell, a manuscript
    sentence, a stage, a task, or the project itself). Replies share the thread's anchor."""

    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    # For example "record:12", "span:40", "cell:3:7:2", "sentence:methods:<hash>", "stage:screening", "project".
    anchor_key: Mapped[str] = mapped_column(String(200), index=True)
    anchor_label: Mapped[str] = mapped_column(String(500), default="", server_default="")
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("comments.id", ondelete="CASCADE"))
    body: Mapped[str] = mapped_column(Text)
    mentions: Mapped[list[int]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    author: Mapped[User | None] = relationship(foreign_keys=[author_id])


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    # "mention", "reply", "task_assigned", "task_due", "stage", "invitation", or "alert"
    kind: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="", server_default="")
    link: Mapped[str] = mapped_column(String(500), default="", server_default="")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship()


class MemberDeclaration(Base):
    """A project member's declaration of competing interests and funding."""

    __tablename__ = "member_declarations"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_member_declaration"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    has_competing_interests: Mapped[bool] = mapped_column(Boolean)
    statement: Mapped[str] = mapped_column(Text, default="", server_default="")
    funding: Mapped[str] = mapped_column(Text, default="", server_default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# --- AI governance (W16) ---


class BenchmarkRun(Base):
    """A run of a model (or of the statistics engine) against a benchmark data set, with metrics and pass or fail
    against the thresholds in force."""

    __tablename__ = "benchmark_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    # "screening", "extraction", "appraisal", or "statistics"
    task: Mapped[str] = mapped_column(String(20))
    dataset_key: Mapped[str] = mapped_column(String(100))
    dataset_name: Mapped[str] = mapped_column(String(300))
    dataset_sha256: Mapped[str] = mapped_column(String(64))
    ai_model_id: Mapped[int | None] = mapped_column(ForeignKey("ai_models.id", ondelete="SET NULL"))
    model: Mapped[str] = mapped_column(String(150), default="", server_default="")
    prompt_version: Mapped[str] = mapped_column(String(60), default="", server_default="")
    # "queued", "running", "completed", or "failed"
    status: Mapped[str] = mapped_column(String(20))
    items: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    processed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    thresholds: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    passed: Mapped[bool | None] = mapped_column(Boolean)
    details: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    error: Mapped[str | None] = mapped_column(Text)
    started_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CalibrationReport(Base):
    """A project's pre-trust check: the AI screens records reviewers have already decided, and its sensitivity and
    specificity are compared with the project's thresholds before AI suggestions are relied on."""

    __tablename__ = "calibration_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(20))
    ai_model_id: Mapped[int | None] = mapped_column(ForeignKey("ai_models.id", ondelete="SET NULL"))
    model: Mapped[str] = mapped_column(String(150))
    prompt_version: Mapped[str] = mapped_column(String(60))
    sample_size: Mapped[int] = mapped_column(Integer)
    seed: Mapped[int] = mapped_column(Integer)
    # "running", "completed", or "failed"
    status: Mapped[str] = mapped_column(String(20))
    # [{"record_id", "human", "ai", "confidence", "error"}]
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    thresholds: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    passed: Mapped[bool | None] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    accepted_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acceptance_note: Mapped[str] = mapped_column(Text, default="", server_default="")


class ReproducibilityCheck(Base):
    """An archived analysis run rerun from its stored script and data, with the results compared."""

    __tablename__ = "reproducibility_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"))
    # "identical", "within_tolerance", "different", or "failed"
    status: Mapped[str] = mapped_column(String(20))
    max_abs_difference: Mapped[float | None] = mapped_column(Float)
    differences: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    r_version: Mapped[str] = mapped_column(String(100), default="", server_default="")
    error: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --- Public API (W17) ---


class ApiToken(Base):
    """A personal access token for the public API. Only its hash is stored; the token is shown once."""

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # "read" and/or "write"
    scopes: Mapped[list[str]] = mapped_column(JSONB)
    # Empty means every project the user belongs to.
    project_ids: Mapped[list[int]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship()


class WebhookSubscription(Base):
    """A URL notified when audit events matching its patterns happen in a project (for example stage.completed)."""

    __tablename__ = "webhook_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(1000))
    # The HMAC signing secret, encrypted (crypto.py).
    secret_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    events: Mapped[list[str]] = mapped_column(JSONB)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    failure_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("webhook_subscriptions.id", ondelete="CASCADE"), index=True)
    audit_event_id: Mapped[int] = mapped_column(ForeignKey("audit_events.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # "pending", "delivered", or "failed"
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    response_status: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    subscription: Mapped[WebhookSubscription] = relationship()
