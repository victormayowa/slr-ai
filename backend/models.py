from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    organization_memberships: Mapped[list["OrganizationMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    organization: Mapped[Organization | None] = relationship()
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
    """Review setup for a project. Versioning and locking arrive with the workflow gates."""

    __tablename__ = "protocols"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), unique=True)
    review_type: Mapped[str] = mapped_column(String(50), default="Systematic Review")
    framework: Mapped[str] = mapped_column(String(20), default="PICO")
    description: Mapped[str] = mapped_column(Text, default="")
    suggested_criteria: Mapped[str] = mapped_column(Text, default="")
    extraction_outline: Mapped[str] = mapped_column(Text, default="")
    rob_tool: Mapped[str] = mapped_column(String(30), default="ROB-2")
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SearchStrategy(Base):
    __tablename__ = "search_strategies"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    database: Mapped[str] = mapped_column(String(100))
    query: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SearchRun(Base):
    """One execution of a database search or one file import; the provenance of every record."""

    __tablename__ = "search_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("search_strategies.id", ondelete="SET NULL"))
    # "database" or "import"
    kind: Mapped[str] = mapped_column(String(10))
    # The database searched, or the imported file's name.
    database: Mapped[str] = mapped_column(String(200))
    source_label: Mapped[str] = mapped_column(String(300))
    query: Mapped[str | None] = mapped_column(Text)
    result_count: Mapped[int] = mapped_column(Integer)
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


class AIRun(Base):
    """Provenance for one AI call: who triggered it, which provider, model, and prompt, and whether it failed."""

    __tablename__ = "ai_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    record_id: Mapped[int | None] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), index=True)
    # "protocol", "screening", "extraction", "appraisal", or "synthesis"
    task: Mapped[str] = mapped_column(String(20))
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(40))
    # "succeeded" or "failed"
    status: Mapped[str] = mapped_column(String(10))
    error: Mapped[str | None] = mapped_column(Text)
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
    # "Include", "Exclude", or "Maybe"
    decision: Mapped[str] = mapped_column(String(10))
    reasoning: Mapped[str] = mapped_column(Text, default="")
    supporting_quote: Mapped[str | None] = mapped_column(Text)


TITLE_ABSTRACT = "title_abstract"


class ScreeningDecision(Base):
    """A reviewer's decision on a record. One per reviewer per stage; changing it replaces the row."""

    __tablename__ = "screening_decisions"
    __table_args__ = (UniqueConstraint("record_id", "stage", "reviewer_id", name="uq_screening_decision"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(20))
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # "include", "exclude", or "undecided"
    decision: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    record: Mapped[Record] = relationship(back_populates="decisions")
    reviewer: Mapped[User] = relationship()


class ExtractionField(Base):
    __tablename__ = "extraction_fields"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_extraction_field_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    position: Mapped[int] = mapped_column(Integer, default=0)


class ExtractionSuggestion(Base):
    __tablename__ = "extraction_suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    ai_run_id: Mapped[int] = mapped_column(ForeignKey("ai_runs.id", ondelete="CASCADE"), index=True)
    field_id: Mapped[int] = mapped_column(ForeignKey("extraction_fields.id", ondelete="CASCADE"))
    value: Mapped[str] = mapped_column(Text)

    field: Mapped[ExtractionField] = relationship()


class AppraisalSuggestion(Base):
    __tablename__ = "appraisal_suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    ai_run_id: Mapped[int] = mapped_column(ForeignKey("ai_runs.id", ondelete="CASCADE"), unique=True)
    tool: Mapped[str] = mapped_column(String(30))
    judgments: Mapped[dict[str, Any]] = mapped_column(JSON)


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
    details: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))

    actor: Mapped[User | None] = relationship()
