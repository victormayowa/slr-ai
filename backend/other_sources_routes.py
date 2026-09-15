"""Records from other methods: citation searching and grey literature. Both can be added while search or screening is
open; new records are checked against the project's records and marked as duplicates automatically."""

import asyncio
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

import models
from audit import record_event
from citation_chasing import chase
from database import get_db
from dedup import find_duplicates
from permissions import Permission
from projects_routes import ProjectAccess, project_access
from rate_limiting import ai_rate_limit
from records_routes import new_record, record_out, run_out, with_record_details
from review_data import final_decision
from search_sources import GREY_LITERATURE_TYPES
from services.errors import SearchError
from workflow import WorkflowError, require_stage_open

router = APIRouter(prefix="/api/projects/{project_id}", tags=["other sources"])

MAX_SEEDS = 200


class CitationSearchRequest(BaseModel):
    # Records to chase; defaults to every record a reviewer included.
    record_ids: list[int] | None = Field(None, max_length=MAX_SEEDS)
    direction: Literal["backward", "forward", "both"] = "both"
    limit_per_seed: int = Field(200, ge=1, le=1000)


class GreyLiteratureEntry(BaseModel):
    source_type: Literal[
        "thesis",
        "conference_abstract",
        "preprint",
        "government_report",
        "regulatory_document",
        "clinical_study_report",
        "trial_register",
        "policy_document",
        "website",
        "other",
    ]
    source_name: str = Field(min_length=1, max_length=200)
    url: str = Field(pattern=r"^https?://\S+$", max_length=1000)
    accessed_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    search_terms: str = Field("", max_length=2000)
    title: str = Field(min_length=1, max_length=2000)
    authors: str = Field("", max_length=5000)
    year: str = Field("", max_length=20)
    abstract: str = Field("", max_length=50_000)


def _require_search_or_screening_open(db: Session, project_id: int) -> None:
    try:
        require_stage_open(db, project_id, "search")
    except WorkflowError:
        try:
            require_stage_open(db, project_id, "screening")
        except WorkflowError as exc:
            raise WorkflowError("Records from other methods can be added while search or screening is open.") from exc


def _mark_new_duplicates(existing: list[models.Record], new: list[models.Record]) -> tuple[int, int]:
    """Mark new records that duplicate a record. Returns (already in the project, repeated within the new records)."""
    duplicates = find_duplicates([*existing, *new])
    new_ids = {record.id for record in new}
    already, repeated = 0, 0
    for record in new:
        if record.id in duplicates:
            record.duplicate_of_id = duplicates[record.id]
            if duplicates[record.id] in new_ids:
                repeated += 1
            else:
                already += 1
    return already, repeated


def _project_records(db: Session, project_id: int) -> list[models.Record]:
    return list(db.scalars(select(models.Record).where(models.Record.project_id == project_id)))


@router.post("/citation-searches", status_code=201, dependencies=[Depends(ai_rate_limit)])
async def run_citation_search(
    body: CitationSearchRequest,
    access: ProjectAccess = Depends(project_access(Permission.RUN_SEARCH)),
    db: Session = Depends(get_db),
):
    """Find the references of chosen records (backward) and the works citing them (forward) through OpenAlex."""
    _require_search_or_screening_open(db, access.project.id)
    records = db.scalars(
        with_record_details(
            select(models.Record).where(
                models.Record.project_id == access.project.id, models.Record.duplicate_of_id.is_(None)
            )
        )
    ).all()
    if body.record_ids is None:
        seeds = [record for record in records if final_decision(record) == "include"]
        if not seeds:
            raise HTTPException(
                status_code=400, detail="Choose records to search from, or include records at screening"
            )
    else:
        by_id = {record.id: record for record in records}
        missing = [record_id for record_id in body.record_ids if record_id not in by_id]
        if missing:
            raise HTTPException(status_code=404, detail="Some records weren't found, or are marked as duplicates")
        seeds = [by_id[record_id] for record_id in dict.fromkeys(body.record_ids)]
    if len(seeds) > MAX_SEEDS:
        raise HTTPException(status_code=400, detail=f"Search from at most {MAX_SEEDS} records at a time")

    try:
        result = await asyncio.to_thread(chase, seeds, body.direction, body.limit_per_seed)
    except SearchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    existing = _project_records(db, access.project.id)
    labels = {"backward": "references", "forward": "citing works", "both": "references and citing works"}
    run = models.SearchRun(
        project_id=access.project.id,
        kind="citation",
        database="OpenAlex",
        source_label=f"Citation searching ({labels[body.direction]}) via OpenAlex",
        connector="openalex",
        interface="OpenAlex API (citation searching)",
        query=f"{labels[body.direction].capitalize()} of {len(seeds)} records",
        result_count=len(result.candidates),
        total_available=len(result.candidates),
        searched_on=datetime.now(UTC).date().isoformat(),
        filters={
            "direction": body.direction,
            "seed_record_ids": [seed.id for seed in seeds],
            "limit_per_seed": body.limit_per_seed,
            "unresolved_seed_record_ids": result.unresolved_seed_ids,
        },
        executed_by_id=access.user.id,
    )
    run.records = [new_record(access.project.id, candidate.record) for candidate in result.candidates]
    db.add(run)
    db.flush()
    for record, candidate in zip(run.records, result.candidates, strict=True):
        for seed_id in sorted(candidate.seed_record_ids):
            direction = "backward" if "backward" in candidate.directions else "forward"
            db.add(
                models.CitationLink(
                    project_id=access.project.id,
                    search_run_id=run.id,
                    seed_record_id=seed_id,
                    record_id=record.id,
                    direction=direction,
                )
            )
    already, repeated = _mark_new_duplicates(existing, list(run.records))
    new_records = len(run.records) - already - repeated
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="search.citations",
        entity_type="search_run",
        entity_id=run.id,
        details={
            "direction": body.direction,
            "seed_records": len(seeds),
            "found": len(run.records),
            "new_records": new_records,
            "already_in_project": already,
            "unresolved_seed_record_ids": result.unresolved_seed_ids,
        },
    )
    db.commit()
    return {
        "run": run_out(run),
        "new_records": new_records,
        "already_in_project": already,
        "repeated_in_results": repeated,
        "unresolved_seed_record_ids": result.unresolved_seed_ids,
    }


@router.post("/grey-literature", status_code=201)
def add_grey_literature(
    body: GreyLiteratureEntry,
    access: ProjectAccess = Depends(project_access(Permission.RUN_SEARCH)),
    db: Session = Depends(get_db),
):
    """Record a report found outside bibliographic databases, with where and when it was found (PRISMA-S)."""
    _require_search_or_screening_open(db, access.project.id)
    existing = _project_records(db, access.project.id)
    source_name = body.source_name.strip()
    run = next(
        (
            candidate
            for candidate in db.scalars(
                select(models.SearchRun)
                .where(
                    models.SearchRun.project_id == access.project.id,
                    models.SearchRun.kind == "other",
                    models.SearchRun.database == source_name,
                    models.SearchRun.searched_on == body.accessed_on,
                )
                .options(selectinload(models.SearchRun.records))
            )
            if candidate.filters.get("source_type") == body.source_type
        ),
        None,
    )
    if run is None:
        run = models.SearchRun(
            project_id=access.project.id,
            kind="other",
            database=source_name,
            source_label=f"{source_name} ({GREY_LITERATURE_TYPES[body.source_type].lower()})",
            query=body.search_terms.strip() or None,
            result_count=0,
            searched_on=body.accessed_on,
            filters={"grey_literature": True, "source_type": body.source_type},
            executed_by_id=access.user.id,
        )
        db.add(run)
    record = models.Record(
        project_id=access.project.id,
        title=body.title.strip(),
        authors=body.authors.strip(),
        year=body.year.strip(),
        abstract=body.abstract.strip(),
        url=body.url,
        identifiers={},
    )
    run.records.append(record)
    run.result_count += 1
    db.flush()
    _mark_new_duplicates(existing, [record])
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="search.grey_literature_added",
        entity_type="record",
        entity_id=record.id,
        details={
            "source_type": body.source_type,
            "source": source_name,
            "url": body.url,
            "accessed_on": body.accessed_on,
            "duplicate_of_id": record.duplicate_of_id,
        },
    )
    db.commit()
    return {"run": run_out(run), "record": record_out(record, access.user.id)}
