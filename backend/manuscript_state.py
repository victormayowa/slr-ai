"""Manuscript state shared by the API and the workflow gate: claim verification, content hashes, versions, author
approvals, and the manuscript stage's requirements and snapshot."""

import hashlib
import json
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from claims import CITATION, CITATION_ID, EvidenceItem, ReferenceState, check_content, unresolved
from evidence_catalog import build_catalog
from manuscript_checklists import CHECKLISTS, checklist_context, complete, evaluate

CREDIT_ROLES = (
    "Conceptualization",
    "Data curation",
    "Formal analysis",
    "Funding acquisition",
    "Investigation",
    "Methodology",
    "Project administration",
    "Resources",
    "Software",
    "Supervision",
    "Validation",
    "Visualization",
    "Writing – original draft",
    "Writing – review & editing",
)
STATEMENT_KEYS = ("funding", "competing_interests", "data_availability", "ethics", "registration", "acknowledgements")


def get_manuscript(db: Session, project_id: int) -> models.Manuscript | None:
    return db.scalar(select(models.Manuscript).where(models.Manuscript.project_id == project_id))


def references(db: Session, project_id: int) -> list[models.ManuscriptReference]:
    return list(
        db.scalars(
            select(models.ManuscriptReference)
            .where(models.ManuscriptReference.project_id == project_id)
            .order_by(models.ManuscriptReference.id)
        )
    )


def reference_states(db: Session, project_id: int) -> dict[int, ReferenceState]:
    return {ref.id: ReferenceState(ref.id, ref.retracted) for ref in references(db, project_id)}


def acknowledgements(db: Session, manuscript: models.Manuscript) -> dict[int, set[str]]:
    rows = db.scalars(
        select(models.ClaimAcknowledgement).where(
            models.ClaimAcknowledgement.section_id.in_([s.id for s in manuscript.sections])
        )
    )
    by_section: dict[int, set[str]] = defaultdict(set)
    for row in rows:
        by_section[row.section_id].add(row.sentence_hash)
    return by_section


def verify(
    db: Session,
    project: models.Project,
    manuscript: models.Manuscript,
    catalog: dict[str, EvidenceItem] | None = None,
) -> dict[str, Any]:
    catalog = catalog if catalog is not None else build_catalog(db, project, manuscript)
    states = reference_states(db, project.id)
    acks = acknowledgements(db, manuscript)
    sections = []
    counts: Counter[str] = Counter()
    total = 0
    for section in sorted(manuscript.sections, key=lambda s: s.position):
        checks = check_content(section.content, catalog, states, acks.get(section.id, set()))
        open_checks = unresolved(checks)
        counts.update("acknowledged" if c.acknowledged else c.status for c in checks)
        sections.append(
            {
                "key": section.key,
                "title": section.title,
                "sentences": [c.out() for c in checks],
                "unresolved": len(open_checks),
            }
        )
        total += len(open_checks)
    return {"sections": sections, "unresolved": total, "counts": dict(counts)}


def cited_ids(manuscript: models.Manuscript) -> set[int]:
    return {
        int(ref_id)
        for section in manuscript.sections
        for match in CITATION.findall(section.content)
        for ref_id in CITATION_ID.findall(match)
    }


def version_content(db: Session, manuscript: models.Manuscript) -> dict[str, Any]:
    cited = cited_ids(manuscript)
    return {
        "title": manuscript.title,
        "citation_style": manuscript.citation_style,
        "keywords": manuscript.keywords,
        "statements": manuscript.statements,
        "sections": {
            s.key: {"title": s.title, "content": s.content}
            for s in sorted(manuscript.sections, key=lambda s: s.position)
        },
        "authors": [
            {
                "name": a.name,
                "email": a.email,
                "affiliation": a.affiliation,
                "orcid": a.orcid,
                "corresponding": a.corresponding,
                "credit_roles": a.credit_roles,
                "competing_interests": a.competing_interests,
            }
            for a in manuscript.authors
        ],
        "references": {str(r.id): r.csl for r in references(db, manuscript.project_id) if r.id in cited},
    }


def content_sha(content: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()


def latest_version(manuscript: models.Manuscript) -> models.ManuscriptVersion | None:
    return max(manuscript.versions, key=lambda v: v.number, default=None)


def approval_state(db: Session, manuscript: models.Manuscript) -> dict[str, Any]:
    version = latest_version(manuscript)
    current = version is not None and version.sha256 == content_sha(version_content(db, manuscript))
    approvals = {a.author_id: a for a in version.approvals} if version else {}
    authors = [
        {
            "author_id": author.id,
            "name": author.name,
            "user_id": author.user_id,
            "approved": author.id in approvals,
            "approved_at": approvals[author.id].created_at if author.id in approvals else None,
            "recorded_by_user_id": approvals[author.id].user_id if author.id in approvals else None,
            "note": approvals[author.id].note if author.id in approvals else "",
        }
        for author in manuscript.authors
    ]
    return {
        "version_id": version.id if version else None,
        "version_number": version.number if version else None,
        "current": current,
        "authors": authors,
        "all_approved": bool(authors) and current and all(a["approved"] for a in authors),
    }


def prisma_checklist(db: Session, project: models.Project, manuscript: models.Manuscript) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(models.ManuscriptChecklistItem).where(
            models.ManuscriptChecklistItem.manuscript_id == manuscript.id,
            models.ManuscriptChecklistItem.checklist == "prisma_2020",
        )
    )
    overrides = {row.item_id: row for row in rows}
    return evaluate(CHECKLISTS["prisma_2020"], manuscript, checklist_context(db, project, manuscript), overrides)


def manuscript_requirements(db: Session, project: models.Project) -> list[tuple[str, bool]]:
    manuscript = get_manuscript(db, project.id)
    if manuscript is None:
        return [("A manuscript drafted from the locked evidence base", False)]
    cited = cited_ids(manuscript)
    refs = [r for r in references(db, project.id) if r.id in cited]
    return [
        ("A manuscript drafted from the locked evidence base", True),
        (
            "Every sentence verified against the evidence or acknowledged",
            verify(db, project, manuscript)["unresolved"] == 0,
        ),
        (
            "Every cited reference checked, and none retracted",
            all(r.verification_status in ("verified", "mismatch") and not r.retracted for r in refs),
        ),
        ("PRISMA 2020 checklist complete", complete(prisma_checklist(db, project, manuscript))),
        ("Every author approved the current manuscript version", approval_state(db, manuscript)["all_approved"]),
    ]


def manuscript_snapshot(db: Session, project: models.Project) -> dict[str, Any]:
    manuscript = get_manuscript(db, project.id)
    if manuscript is None:
        return {}
    version = latest_version(manuscript)
    return {
        "manuscript_id": manuscript.id,
        "version_id": version.id if version else None,
        "version_sha256": version.sha256 if version else None,
        "evidence_snapshot_id": manuscript.evidence_snapshot_id,
        "approvals": approval_state(db, manuscript),
        "prisma_2020": [
            {k: item[k] for k in ("item_id", "status", "location")}
            for item in prisma_checklist(db, project, manuscript)
        ],
        "references": [
            {"id": r.id, "doi": r.doi, "verification_status": r.verification_status, "retracted": r.retracted}
            for r in references(db, project.id)
            if r.id in cited_ids(manuscript)
        ],
    }
