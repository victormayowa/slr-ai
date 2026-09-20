"""The protocol as a document (Markdown or Word), and mapped to the fields of the PROSPERO registration form.

Everything comes from what the project records. Gaps are marked [TO COMPLETE] rather than filled in.
"""

from dataclasses import dataclass, field
from io import BytesIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import models
from ai_catalog import model_ref
from docx_style import styled_document
from protocol_design import project_criteria, project_sections
from protocol_frameworks import (
    FRAMEWORKS,
    GENERAL_CRITERION_ELEMENTS,
    OUTCOME_PRIORITIES,
    PLACEHOLDER_MARKER,
    PROTOCOL_SECTIONS,
    SECTIONS_BY_KEY,
    SYNTHESIS_APPROACHES,
)

TO_COMPLETE = "[TO COMPLETE]"


@dataclass
class Block:
    heading: str
    paragraphs: list[str] = field(default_factory=list)
    bullets: list[str] = field(default_factory=list)
    # The first row is the header.
    table: list[list[str]] | None = None


def latest_protocol_snapshot(db: Session, project_id: int) -> models.StageSnapshot | None:
    return db.scalar(
        select(models.StageSnapshot)
        .where(models.StageSnapshot.project_id == project_id, models.StageSnapshot.stage == "protocol")
        .order_by(models.StageSnapshot.version.desc())
        .limit(1)
    )


def protocol_is_locked(db: Session, project_id: int) -> bool:
    row = db.scalar(
        select(models.ProjectStage).where(
            models.ProjectStage.project_id == project_id, models.ProjectStage.stage == "protocol"
        )
    )
    return row is not None and row.completed_at is not None


def latest_protocol_version(db: Session, project_id: int) -> int:
    return (
        db.scalar(
            select(func.max(models.StageSnapshot.version)).where(
                models.StageSnapshot.project_id == project_id, models.StageSnapshot.stage == "protocol"
            )
        )
        or 0
    )


def _element_labels(framework_key: str) -> dict[str, str]:
    framework = FRAMEWORKS.get(framework_key)
    labels = {element.key: element.label for element in GENERAL_CRITERION_ELEMENTS}
    labels.update({element.key: element.label for element in framework.elements} if framework else {})
    return labels


def _outcome_line(outcome: dict) -> str:
    details = ", ".join(part for part in (outcome.get("timepoint"), outcome.get("measure")) if part)
    return f"{outcome['name']}" + (f" ({details})" if details else "")


def protocol_document(db: Session, project: models.Project) -> tuple[str, list[Block]]:
    """The protocol's title and blocks, in PRISMA-P order, with the structured parts placed in their sections."""
    protocol = project.protocol
    if protocol is None:
        return project.title, [Block("Protocol", [TO_COMPLETE])]
    snapshot = latest_protocol_snapshot(db, project.id)
    if protocol_is_locked(db, project.id) and snapshot is not None:
        status = (
            f"Version {snapshot.version}, locked on {snapshot.created_at:%Y-%m-%d} "
            f"(SHA-256 {snapshot.sha256[:16]}…). Changes after this version are recorded as amendments."
        )
    else:
        status = "Draft: this protocol isn't locked yet, so it may change."

    sections = project_sections(db, project.id)
    labels = _element_labels(protocol.framework)
    framework = FRAMEWORKS.get(protocol.framework)
    accepted = [c for c in project_criteria(db, project.id) if c.status == "accepted"]
    strategies = db.scalars(
        select(models.SearchStrategy)
        .where(models.SearchStrategy.project_id == project.id)
        .order_by(models.SearchStrategy.id)
    ).all()
    plan = protocol.analysis_plan or {}

    blocks = [Block("Protocol status", [status])]
    for section in PROTOCOL_SECTIONS:
        row = sections.get(section.key)
        block = Block(section.label, [row.content] if row and row.content else [])
        if section.key == "eligibility":
            block.bullets = [
                f"{'Include' if c.kind == 'inclusion' else 'Exclude'}: {c.text}"
                + (f" ({labels.get(c.element, c.element)})" if c.element else "")
                for c in sorted(accepted, key=lambda c: c.kind != "inclusion")
            ]
        elif section.key == "search_strategy":
            block.paragraphs += [f"{s.database}: {s.query}" for s in strategies]
        elif section.key == "data_items":
            block.bullets = [f.name for f in project.extraction_fields]
        elif section.key == "outcomes" and plan.get("outcomes"):
            block.table = [["Outcome", "Priority", "Timepoint", "Effect measure"]] + [
                [
                    o["name"],
                    OUTCOME_PRIORITIES.get(o["priority"], o["priority"]),
                    o.get("timepoint", ""),
                    o.get("measure", ""),
                ]
                for o in plan["outcomes"]
            ]
        elif section.key == "risk_of_bias":
            block.paragraphs.append(f"Risk of bias tool: {protocol.rob_tool}")
        elif section.key == "synthesis":
            approach = SYNTHESIS_APPROACHES.get(plan.get("synthesis_approach", "undecided"), "Not decided yet")
            block.paragraphs.append(f"Planned synthesis: {approach}.")
            if plan.get("heterogeneity"):
                block.paragraphs.append(f"Heterogeneity and model choice: {plan['heterogeneity']}")
            block.bullets = [f"Subgroup analysis: {a['name']}" for a in plan.get("subgroups", [])] + [
                f"Sensitivity analysis: {a['name']}" for a in plan.get("sensitivity_analyses", [])
            ]
        elif section.key == "ai_use" and not block.paragraphs:
            block.paragraphs.append(
                f"Models pinned for this project: {model_ref(project.ai_model) or 'none'} for text tasks and "
                f"{model_ref(project.embedding_model) or 'none'} for embeddings. "
                f"{TO_COMPLETE}: how reviewers check AI output."
            )
        if not (block.paragraphs or block.bullets or block.table):
            block.paragraphs = [TO_COMPLETE + (" (required)" if section.required else "")]
        blocks.append(block)
        if section.key == "objectives":
            question = [protocol.question or TO_COMPLETE]
            elements = [
                f"{element.label}: {protocol.question_elements.get(element.key) or TO_COMPLETE}"
                for element in (framework.elements if framework else ())
            ]
            blocks.append(Block(f"Review question ({protocol.framework})", question, elements))

    ai_assisted = [SECTIONS_BY_KEY[key].label for key, row in sections.items() if row.based_on_suggestion_id]
    if ai_assisted:
        blocks.append(
            Block(
                "Disclosure of AI assistance",
                [f"These sections began as AI drafts that reviewers checked and edited: {', '.join(ai_assisted)}."],
            )
        )
    return f"{project.title}: protocol for a {protocol.review_type.lower()}", blocks


def _escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def to_markdown(title: str, blocks: list[Block]) -> str:
    lines = [f"# {title}", ""]
    for block in blocks:
        lines += [f"## {block.heading}", ""]
        for paragraph in block.paragraphs:
            lines += [paragraph, ""]
        if block.bullets:
            lines += [f"- {bullet}" for bullet in block.bullets] + [""]
        if block.table:
            header, *rows = block.table
            lines.append("| " + " | ".join(_escape_cell(cell) for cell in header) + " |")
            lines.append("|" + "---|" * len(header))
            lines += ["| " + " | ".join(_escape_cell(cell) for cell in row) + " |" for row in rows]
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def to_docx(title: str, blocks: list[Block]) -> bytes:
    document = styled_document()
    document.add_heading(title, level=0)
    for block in blocks:
        document.add_heading(block.heading, level=1)
        for paragraph in block.paragraphs:
            for part in (part.strip() for part in paragraph.split("\n\n")):
                if part:
                    document.add_paragraph(part)
        for bullet in block.bullets:
            document.add_paragraph(bullet, style="List Bullet")
        if block.table:
            table = document.add_table(rows=len(block.table), cols=len(block.table[0]))
            table.style = "Table Grid"
            for row_cells, values in zip(table.rows, block.table, strict=True):
                for cell, value in zip(row_cells.cells, values, strict=True):
                    cell.text = value
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def prospero_fields(db: Session, project: models.Project) -> list[dict]:
    """The protocol mapped to PROSPERO registration form fields, for reviewers to copy into PROSPERO."""
    protocol = project.protocol
    if protocol is None:
        return []
    sections = {key: row.content for key, row in project_sections(db, project.id).items()}
    elements = protocol.question_elements or {}
    labels = _element_labels(protocol.framework)
    accepted = [c for c in project_criteria(db, project.id) if c.status == "accepted"]
    plan = protocol.analysis_plan or {}
    outcomes = plan.get("outcomes") or []
    strategies = db.scalars(
        select(models.SearchStrategy)
        .where(models.SearchStrategy.project_id == project.id)
        .order_by(models.SearchStrategy.id)
    ).all()

    def element_text(*keys: str) -> str:
        return "\n".join(f"{labels.get(key, key)}: {elements[key]}" for key in keys if elements.get(key))

    def criteria_text(*keys: str) -> str:
        return "\n".join(
            f"{'Inclusion' if c.kind == 'inclusion' else 'Exclusion'}: {c.text}" for c in accepted if c.element in keys
        )

    def joined(*parts: str) -> str:
        return "\n\n".join(part.strip() for part in parts if part and part.strip())

    screening_started = db.scalar(
        select(func.count())
        .select_from(models.ScreeningDecision)
        .join(models.Record)
        .where(models.Record.project_id == project.id)
    )
    approach = SYNTHESIS_APPROACHES.get(plan.get("synthesis_approach", "undecided"))
    rows = [
        ("Review title", project.title, "Project title"),
        ("Review question", protocol.question, "Review question"),
        ("Condition or domain being studied", protocol.description, "Study description; check it names the condition"),
        (
            "Searches",
            joined(sections.get("information_sources", ""), "Databases: " + ", ".join(s.database for s in strategies)),
            "Information sources section and search strategies",
        ),
        ("Search strategy", "\n\n".join(f"{s.database}: {s.query}" for s in strategies), "Search strategies"),
        (
            "Participants or population",
            joined(element_text("population", "sample"), criteria_text("population", "sample")),
            "Question and criteria",
        ),
        (
            "Intervention(s) or exposure(s)",
            joined(
                element_text("intervention", "exposure", "phenomenon_of_interest", "concept"),
                criteria_text("intervention", "exposure", "phenomenon_of_interest", "concept"),
            ),
            "Question and criteria",
        ),
        (
            "Comparator(s) or control(s)",
            joined(element_text("comparator"), criteria_text("comparator")),
            "Question and criteria",
        ),
        (
            "Types of study to be included",
            joined(element_text("study_design", "design"), criteria_text("study_design", "design")),
            "Question and criteria",
        ),
        ("Context", joined(element_text("context"), criteria_text("context", "setting")), "Question and criteria"),
        (
            "Main outcome(s)",
            "\n".join(_outcome_line(o) for o in outcomes if o["priority"] == "primary"),
            "Analysis plan",
        ),
        (
            "Additional outcome(s)",
            "\n".join(_outcome_line(o) for o in outcomes if o["priority"] != "primary"),
            "Analysis plan",
        ),
        (
            "Data extraction (selection and coding)",
            joined(sections.get("selection_process", ""), sections.get("data_collection", "")),
            "Selection and data collection sections",
        ),
        (
            "Risk of bias (quality) assessment",
            joined(sections.get("risk_of_bias", ""), f"Tool: {protocol.rob_tool}"),
            "Risk of bias section and tool",
        ),
        (
            "Strategy for data synthesis",
            joined(
                sections.get("synthesis", ""),
                f"Planned synthesis: {approach}" if approach else "",
                plan.get("heterogeneity", ""),
            ),
            "Data synthesis section and analysis plan",
        ),
        (
            "Analysis of subgroups or subsets",
            "\n".join(
                a["name"] + (f": {a['rationale']}" if a.get("rationale") else "") for a in plan.get("subgroups", [])
            ),
            "Analysis plan",
        ),
        ("Review team members and affiliations", sections.get("authors", ""), "Authors section"),
        ("Funding sources and sponsors", sections.get("support", ""), "Support and funding section"),
        ("Conflicts of interest", sections.get("competing_interests", ""), "Competing interests section"),
        ("Dissemination plans", sections.get("ethics_and_dissemination", ""), "Ethics and dissemination section"),
        (
            "Stage of review at time of submission",
            "Screening against the eligibility criteria has started."
            if screening_started
            else "Screening against the eligibility criteria has not started.",
            "Screening decisions recorded in OmniReview",
        ),
        ("Anticipated start and completion dates", "", "Not recorded in OmniReview; enter them in PROSPERO"),
    ]
    return [
        {
            "field": name,
            "value": value.strip(),
            "source": source,
            "ready": bool(value.strip()) and PLACEHOLDER_MARKER not in value and TO_COMPLETE not in value,
        }
        for name, value, source in rows
    ]
