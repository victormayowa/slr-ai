"""Rules a protocol is checked against before it's locked, and the project summary given to AI protocol tasks."""

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from ai_catalog import model_ref
from protocol_frameworks import (
    FINER_CRITERIA,
    FRAMEWORKS,
    PLACEHOLDER_MARKER,
    PROTOCOL_SECTIONS,
    REQUIRED_SECTIONS,
    SECTIONS_BY_KEY,
)

# Elements a criterion doesn't need to restrict: comparators and outcomes often don't limit eligibility.
_ELEMENTS_WITHOUT_REQUIRED_CRITERIA = {"comparator", "outcomes", "evaluation", "research_type"}


@dataclass
class ProtocolIssue:
    code: str
    severity: Literal["error", "warning"]
    message: str
    criterion_ids: list[int] = field(default_factory=list)
    elements: list[str] = field(default_factory=list)


def _normalized(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def project_criteria(db: Session, project_id: int) -> list[models.Criterion]:
    return list(
        db.scalars(
            select(models.Criterion).where(models.Criterion.project_id == project_id).order_by(models.Criterion.id)
        )
    )


def project_sections(db: Session, project_id: int) -> dict[str, models.ProtocolSection]:
    rows = db.scalars(select(models.ProtocolSection).where(models.ProtocolSection.project_id == project_id))
    return {row.key: row for row in rows}


def _question_issues(protocol: models.Protocol) -> list[ProtocolIssue]:
    issues = []
    if not protocol.question.strip():
        issues.append(ProtocolIssue("question_missing", "error", "Write the review question as one sentence"))
    framework = FRAMEWORKS.get(protocol.framework)
    if framework is None:
        issues.append(ProtocolIssue("framework_unknown", "error", "Choose a question framework"))
        return issues
    for element in framework.elements:
        if not protocol.question_elements.get(element.key, "").strip():
            issues.append(
                ProtocolIssue(
                    "element_missing",
                    "error",
                    f"Describe the {element.label.lower()} in the review question",
                    elements=[element.key],
                )
            )
    return issues


def _criteria_issues(protocol: models.Protocol, criteria: list[models.Criterion]) -> list[ProtocolIssue]:
    issues = []
    pending = [criterion.id for criterion in criteria if criterion.status == "pending"]
    if pending:
        issues.append(
            ProtocolIssue(
                "criteria_pending",
                "error",
                f"{len(pending)} suggested criteria still need to be accepted or rejected",
                criterion_ids=pending,
            )
        )
    accepted = [criterion for criterion in criteria if criterion.status == "accepted"]
    inclusion = {_normalized(c.text): c for c in accepted if c.kind == "inclusion"}
    for exclusion in (c for c in accepted if c.kind == "exclusion"):
        included = inclusion.get(_normalized(exclusion.text))
        if included is not None:
            issues.append(
                ProtocolIssue(
                    "contradictory_criteria",
                    "error",
                    f'"{exclusion.text}" is both an inclusion and an exclusion criterion',
                    criterion_ids=[included.id, exclusion.id],
                )
            )
    unlinked = [criterion.id for criterion in accepted if not criterion.element]
    if unlinked:
        issues.append(
            ProtocolIssue(
                "criteria_unlinked",
                "warning",
                f"{len(unlinked)} accepted criteria aren't linked to a part of the review question",
                criterion_ids=unlinked,
            )
        )
    framework = FRAMEWORKS.get(protocol.framework)
    for element in framework.elements if framework else ():
        if element.key in _ELEMENTS_WITHOUT_REQUIRED_CRITERIA or not protocol.question_elements.get(element.key):
            continue
        if not any(c.kind == "inclusion" and c.element == element.key for c in accepted):
            issues.append(
                ProtocolIssue(
                    "element_without_criteria",
                    "warning",
                    f"No accepted inclusion criterion covers the {element.label.lower()}",
                    elements=[element.key],
                )
            )
    return issues


def _plan_issues(protocol: models.Protocol) -> list[ProtocolIssue]:
    issues = []
    plan = protocol.analysis_plan or {}
    if not any(outcome.get("priority") == "primary" for outcome in plan.get("outcomes") or []):
        issues.append(
            ProtocolIssue(
                "no_primary_outcome", "error", "Pre-specify at least one primary outcome in the analysis plan"
            )
        )
    if plan.get("synthesis_approach", "undecided") == "undecided":
        issues.append(
            ProtocolIssue(
                "synthesis_undecided", "warning", "Choose the planned synthesis approach in the analysis plan"
            )
        )
    return issues


def _section_issues(sections: dict[str, models.ProtocolSection]) -> list[ProtocolIssue]:
    issues = []
    for section in REQUIRED_SECTIONS:
        row = sections.get(section.key)
        if row is None or not row.content.strip():
            issues.append(
                ProtocolIssue("section_missing", "error", f"Write the {section.label} section of the protocol document")
            )
    for section in PROTOCOL_SECTIONS:
        row = sections.get(section.key)
        if row is not None and PLACEHOLDER_MARKER in row.content:
            issues.append(
                ProtocolIssue(
                    "section_placeholder", "warning", f"The {section.label} section still has placeholders to complete"
                )
            )
    return issues


def _finer_issues(protocol: models.Protocol) -> list[ProtocolIssue]:
    issues = []
    finer = protocol.finer or {}
    unassessed = [criterion.label for criterion in FINER_CRITERIA if criterion.key not in finer]
    if unassessed:
        issues.append(
            ProtocolIssue("finer_incomplete", "warning", f"FINER assessment not yet rated: {', '.join(unassessed)}")
        )
    for criterion in FINER_CRITERIA:
        assessment = finer.get(criterion.key) or {}
        if assessment.get("rating") == "no":
            note = f": {assessment['note']}" if assessment.get("note") else ""
            issues.append(
                ProtocolIssue("finer_concern", "warning", f"The question was rated not {criterion.label.lower()}{note}")
            )
    return issues


def protocol_issues(db: Session, project: models.Project) -> list[ProtocolIssue]:
    """Problems to fix before locking the protocol. Errors block sign-off; warnings are for reviewers to weigh."""
    protocol = project.protocol
    if protocol is None:
        return [ProtocolIssue("no_protocol", "error", "This project has no protocol")]
    return [
        *_question_issues(protocol),
        *_criteria_issues(protocol, project_criteria(db, project.id)),
        *_plan_issues(protocol),
        *_section_issues(project_sections(db, project.id)),
        *_finer_issues(protocol),
    ]


def protocol_context(db: Session, project: models.Project, exclude_section: str | None = None) -> dict[str, Any]:
    """Everything decided so far about the protocol, as given to AI drafting and consistency checks."""
    protocol = project.protocol
    if protocol is None:
        return {"title": project.title}
    framework = FRAMEWORKS.get(protocol.framework)
    elements = protocol.question_elements or {}
    strategies = db.scalars(
        select(models.SearchStrategy)
        .where(models.SearchStrategy.project_id == project.id)
        .order_by(models.SearchStrategy.id)
    )
    sections = project_sections(db, project.id)
    return {
        "title": project.title,
        "review_type": protocol.review_type,
        "study_description": protocol.description,
        "question_framework": protocol.framework,
        "review_question": protocol.question,
        "question_elements": {
            element.key: {"label": element.label, "text": elements.get(element.key, "")}
            for element in (framework.elements if framework else ())
        },
        "finer_assessment": protocol.finer,
        "accepted_criteria": [
            {"id": c.id, "kind": c.kind, "element": c.element, "text": c.text}
            for c in project_criteria(db, project.id)
            if c.status == "accepted"
        ],
        "search_strategies": [{"database": s.database, "query": s.query} for s in strategies],
        "extraction_fields": [field.name for field in project.extraction_fields],
        "risk_of_bias_tool": protocol.rob_tool,
        "analysis_plan": protocol.analysis_plan,
        "ai_models": {"text": model_ref(project.ai_model), "embeddings": model_ref(project.embedding_model)},
        "other_sections": {
            SECTIONS_BY_KEY[key].label: row.content
            for key, row in sections.items()
            if key != exclude_section and key in SECTIONS_BY_KEY and row.content.strip()
        },
    }
