"""Interoperability routes: record exports and imports, decisions exchanged with Rayyan and Covidence, RevMan-style
and GRADEpro-style spreadsheets, the manuscript as JATS XML, and the EBMonFHIR bundle."""

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

import entitlements
import fhir
import interop
import models
from audit import record_event
from certainty_routes import SOF_COLUMNS, sof_table_rows, summary_of_findings
from database import get_db
from manuscript_state import get_manuscript, references
from permissions import Permission
from projects_routes import ProjectAccess, get_in_project, project_access
from review_data import FULL_TEXT, TITLE_ABSTRACT, ReviewPolicy
from review_settings import load_settings
from services.record_import import ImportFormatError
from workflow import require_stage_open

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["interoperability"])

MAX_IMPORT_RECORDS = 20_000
Scope = Literal["all", "unique", "included"]


def _records(db: Session, project_id: int, scope: Scope, policy: ReviewPolicy) -> list[models.Record]:
    query = (
        select(models.Record)
        .where(models.Record.project_id == project_id)
        .options(selectinload(models.Record.decisions), selectinload(models.Record.adjudications))
        .order_by(models.Record.id)
    )
    if scope != "all":
        query = query.where(models.Record.duplicate_of_id.is_(None))
    records = list(db.scalars(query))
    if scope == "included":
        records = [record for record in records if policy.included(record)]
    return records


def _final_decisions(records: list[models.Record], policy: ReviewPolicy, stage: str) -> dict[int, str]:
    decisions = {}
    for record in records:
        status = policy.status(record, stage)
        if status.final in ("include", "exclude"):
            decisions[record.id] = status.final
    return decisions


def _file_response(content: bytes, name: str, media_type: str) -> Response:
    return Response(
        content=content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


@router.get("/records/export")
def export_records(
    format: Literal["ris", "bibtex", "endnote_xml", "csv", "json"] = "ris",
    scope: Scope = "unique",
    stage: Literal["title_abstract", "full_text"] = "title_abstract",
    access: ProjectAccess = Depends(project_access(Permission.EXPORT)),
    db: Session = Depends(get_db),
):
    """Export the project's records for a reference manager or another screening tool."""
    settings = load_settings(db, access.project.id)
    policy = ReviewPolicy(settings.screening.title_abstract_reviewers, settings.screening.full_text_reviewers)
    records = _records(db, access.project.id, scope, policy)
    stage_key = TITLE_ABSTRACT if stage == "title_abstract" else FULL_TEXT
    content = interop.export_records(records, format, _final_decisions(records, policy, stage_key))
    name = f"records-{scope}-project-{access.project.id}.{interop.RECORD_EXTENSIONS[format]}"
    return _file_response(content, name, interop.RECORD_MEDIA_TYPES[format])


@router.get("/records/export/rayyan")
def export_for_rayyan(
    scope: Scope = "unique",
    reviewer: str = Query("OmniReview", min_length=1, max_length=100),
    access: ProjectAccess = Depends(project_access(Permission.EXPORT)),
    db: Session = Depends(get_db),
):
    """A Rayyan-style CSV whose RAYYAN-INCLUSION column carries this review's decisions under the given reviewer."""
    settings = load_settings(db, access.project.id)
    policy = ReviewPolicy(settings.screening.title_abstract_reviewers, settings.screening.full_text_reviewers)
    records = _records(db, access.project.id, scope, policy)
    content = interop.rayyan_csv(records, _final_decisions(records, policy, TITLE_ABSTRACT), reviewer.strip())
    return _file_response(content, f"rayyan-project-{access.project.id}.csv", "text/csv")


@router.get("/records/export/covidence")
def export_for_covidence(
    scope: Scope = "unique",
    stage: Literal["title_abstract", "full_text"] = "title_abstract",
    access: ProjectAccess = Depends(project_access(Permission.EXPORT)),
    db: Session = Depends(get_db),
):
    """A Covidence-style study CSV with each record's decision at the chosen stage."""
    settings = load_settings(db, access.project.id)
    policy = ReviewPolicy(settings.screening.title_abstract_reviewers, settings.screening.full_text_reviewers)
    records = _records(db, access.project.id, scope, policy)
    stage_key = TITLE_ABSTRACT if stage == "title_abstract" else FULL_TEXT
    content = interop.covidence_csv(records, _final_decisions(records, policy, stage_key), stage_key)
    return _file_response(content, f"covidence-project-{access.project.id}.csv", "text/csv")


@router.post("/records/import/structured", status_code=201)
async def import_structured_records(
    file: UploadFile = File(...),
    format: Literal["json", "jats"] = "json",
    access: ProjectAccess = Depends(project_access(Permission.RUN_SEARCH)),
    db: Session = Depends(get_db),
):
    """Import records from JSON or JATS XML, alongside the reference formats in the search screen."""
    require_stage_open(db, access.project.id, "search")
    file_name = (file.filename or "upload")[:200]
    content = await file.read()
    parser = interop.parse_json_records if format == "json" else interop.parse_jats_records
    try:
        parsed = await asyncio.to_thread(parser, content)
    except ImportFormatError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if len(parsed) > MAX_IMPORT_RECORDS:
        raise HTTPException(status_code=400, detail=f"{file_name} has more than {MAX_IMPORT_RECORDS} records")
    entitlements.require(db, entitlements.account_for_project(access.project), "records_per_month", len(parsed))
    run = models.SearchRun(
        project_id=access.project.id,
        kind="import",
        database=file_name,
        source_label=f"{format.upper()} import ({file_name})",
        file_format=format,
        result_count=len(parsed),
        filters={"file_name": file_name},
        executed_by_id=access.user.id,
    )
    run.records = [models.Record(project_id=access.project.id, **record) for record in parsed]
    db.add(run)
    db.flush()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="records.imported",
        entity_type="search_run",
        entity_id=run.id,
        details={"file_name": file_name, "format": format, "records": len(parsed)},
    )
    db.commit()
    return {"search_run_id": run.id, "records": len(parsed), "format": format}


@router.post("/screening/import-decisions")
async def import_decisions(
    file: UploadFile = File(...),
    stage: Literal["title_abstract", "full_text"] = "title_abstract",
    dry_run: bool = False,
    access: ProjectAccess = Depends(project_access(Permission.SCREEN)),
    db: Session = Depends(get_db),
):
    """Bring screening decisions back from Rayyan or Covidence, matched by DOI, record id, or title.

    The decisions are recorded as this reviewer's, so the review's own gates and conflict handling still apply.
    """
    stage_key = TITLE_ABSTRACT if stage == "title_abstract" else FULL_TEXT
    require_stage_open(db, access.project.id, "screening" if stage_key == TITLE_ABSTRACT else "full_text_screening")
    content = await file.read()
    try:
        rows = await asyncio.to_thread(interop.parse_decision_file, content)
    except ImportFormatError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    records = db.scalars(
        select(models.Record)
        .where(models.Record.project_id == access.project.id, models.Record.duplicate_of_id.is_(None))
        .options(selectinload(models.Record.decisions))
    ).all()
    by_doi = {record.doi.lower(): record for record in records if record.doi}
    by_id = {str(record.id): record for record in records}
    by_title = {" ".join(record.title.lower().split()): record for record in records}
    settings = load_settings(db, access.project.id)
    policy = ReviewPolicy(settings.screening.title_abstract_reviewers, settings.screening.full_text_reviewers)
    applied, unmatched, skipped = 0, [], 0
    for row in rows:
        record = (
            by_doi.get(row["doi"].lower())
            or by_id.get(row["key"])
            or by_doi.get(row["key"].lower())
            or by_title.get(" ".join(row["title"].lower().split()))
        )
        if record is None:
            unmatched.append(row["title"] or row["key"] or row["doi"])
            continue
        if row["decision"] == "undecided":
            skipped += 1
            continue
        if stage_key == FULL_TEXT and not policy.sought(record):
            skipped += 1
            continue
        existing = next((d for d in record.decisions if d.stage == stage_key and d.reviewer_id == access.user.id), None)
        if existing is not None and existing.decision == row["decision"]:
            continue
        if not dry_run:
            if existing is not None:
                record.decisions.remove(existing)
                db.flush()
            reason = "other" if row["decision"] == "exclude" and stage_key == FULL_TEXT else None
            record.decisions.append(
                models.ScreeningDecision(
                    stage=stage_key,
                    reviewer_id=access.user.id,
                    decision=row["decision"],
                    reason_code=reason,
                    note="Imported from another screening tool",
                )
            )
        applied += 1
    if not dry_run and applied:
        record_event(
            db,
            project_id=access.project.id,
            actor_id=access.user.id,
            action="screening.decisions_imported",
            entity_type="project",
            entity_id=access.project.id,
            details={"stage": stage_key, "applied": applied, "unmatched": len(unmatched), "skipped": skipped},
        )
        db.commit()
    else:
        db.rollback()
    return {
        "rows": len(rows),
        "applied": applied,
        "skipped": skipped,
        "unmatched": unmatched[:50],
        "unmatched_count": len(unmatched),
        "dry_run": dry_run,
    }


@router.get("/exports/revman.csv")
def export_revman(
    analysis_id: int | None = None,
    access: ProjectAccess = Depends(project_access(Permission.EXPORT)),
    db: Session = Depends(get_db),
):
    """A RevMan-style data sheet of the analysis rows, one row per study, for pasting into a RevMan comparison."""
    query = (
        select(models.Analysis)
        .where(models.Analysis.project_id == access.project.id)
        .options(selectinload(models.Analysis.runs))
        .order_by(models.Analysis.id)
    )
    if analysis_id is not None:
        analyses = [get_in_project(db, models.Analysis, analysis_id, access.project.id, "Analysis")]
    else:
        analyses = list(db.scalars(query))
    rows = []
    for analysis in analyses:
        run = next((r for r in reversed(analysis.runs) if r.status == "succeeded"), None)
        if run is None:
            continue
        for item in run.dataset.get("rows", []):
            rows.append(
                {
                    "Comparison": analysis.title,
                    "Outcome": analysis.outcome,
                    "Study": item.get("study") or item.get("study_id", ""),
                    "Year": item.get("year", ""),
                    "Events experimental": item.get("ai", ""),
                    "Total experimental": item.get("n1i", ""),
                    "Events control": item.get("ci", ""),
                    "Total control": item.get("n2i", ""),
                    "Mean experimental": item.get("m1i", ""),
                    "SD experimental": item.get("sd1i", ""),
                    "Mean control": item.get("m2i", ""),
                    "SD control": item.get("sd2i", ""),
                    "Risk of bias": item.get("rob", ""),
                }
            )
    if not rows:
        raise HTTPException(status_code=409, detail="No analysis has been run yet, so there is nothing to export")
    return _file_response(interop.revman_csv(rows), f"revman-project-{access.project.id}.csv", "text/csv")


@router.get("/exports/gradepro.csv")
def export_gradepro(access: ProjectAccess = Depends(project_access(Permission.EXPORT)), db: Session = Depends(get_db)):
    """A GRADEpro-style summary of findings sheet, matching the table shown in the certainty screen."""
    rows = summary_of_findings(db, access.project)
    if not rows:
        raise HTTPException(status_code=409, detail="No outcome has been graded yet")
    content = interop.gradepro_csv(SOF_COLUMNS, sof_table_rows(rows))
    return _file_response(content, f"gradepro-sof-project-{access.project.id}.csv", "text/csv")


@router.get("/manuscript/export/jats")
def export_manuscript_jats(
    access: ProjectAccess = Depends(project_access(Permission.EXPORT)), db: Session = Depends(get_db)
):
    """The manuscript as JATS XML, for journals and repositories that accept it."""
    manuscript = get_manuscript(db, access.project.id)
    if manuscript is None:
        raise HTTPException(status_code=404, detail="This project has no manuscript yet")
    abstract = next((section.content for section in manuscript.sections if section.key == "abstract"), "")
    sections = [(section.title, section.content) for section in manuscript.sections if section.key != "abstract"]
    authors = [
        {"name": author.name, "affiliation": author.affiliation, "orcid": author.orcid} for author in manuscript.authors
    ]
    content = interop.manuscript_jats(
        manuscript.title,
        abstract,
        sections,
        [reference.csl for reference in references(db, access.project.id)],
        authors,
    )
    return _file_response(content, f"manuscript-project-{access.project.id}.xml", "application/xml")


@router.get("/fhir/bundle")
def fhir_bundle(access: ProjectAccess = Depends(project_access(Permission.EXPORT)), db: Session = Depends(get_db)):
    """The review as an EBMonFHIR bundle: Citation, EvidenceVariable, and Evidence resources."""
    try:
        return fhir.build_bundle(db, access.project)
    except ValueError as exc:
        logger.error("Building the FHIR bundle for project %s failed: %s", access.project.id, exc)
        raise HTTPException(status_code=409, detail=f"The bundle couldn't be built: {exc}") from exc
