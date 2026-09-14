"""Database searches, file imports, the record list, deduplication, and PRISMA counts."""

import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql import Select

import models
from audit import record_event
from database import get_db
from permissions import Permission
from projects_routes import ProjectAccess, get_in_project, project_access
from rate_limiting import ai_rate_limit
from services.errors import SearchError
from services.openalex import search_openalex
from services.pubmed import search_pubmed

router = APIRouter(prefix="/api/projects/{project_id}", tags=["records"])

TITLE_ABSTRACT = models.TITLE_ABSTRACT
MAX_IMPORT_RECORDS = 5000


class SearchRunRequest(BaseModel):
    strategy_id: int
    limit: int = Field(50, ge=1, le=200)


class ImportedRecord(BaseModel):
    title: str = Field(min_length=1, max_length=2000)
    authors: str = Field("", max_length=5000)
    year: str = Field("", max_length=20)
    venue: str = Field("", max_length=1000)
    doi: str = Field("", max_length=255)
    abstract: str = Field("", max_length=50_000)


class ImportRequest(BaseModel):
    file_name: str = Field(min_length=1, max_length=200)
    records: list[ImportedRecord] = Field(min_length=1, max_length=MAX_IMPORT_RECORDS)


def with_record_details(query: Select) -> Select:
    """Eager-load everything record_out needs, avoiding a query per record."""
    return query.options(
        selectinload(models.Record.search_run),
        selectinload(models.Record.decisions),
        selectinload(models.Record.ai_runs).selectinload(models.AIRun.screening),
        selectinload(models.Record.ai_runs).selectinload(models.AIRun.appraisal),
        selectinload(models.Record.ai_runs)
        .selectinload(models.AIRun.extraction_values)
        .selectinload(models.ExtractionSuggestion.field),
    )


def final_decision(record: models.Record) -> str | None:
    """The most recent reviewer decision at title/abstract screening. AI suggestions never count."""
    decisions = [d for d in record.decisions if d.stage == TITLE_ABSTRACT]
    return max(decisions, key=lambda d: d.id).decision if decisions else None


def _latest_run(record: models.Record, task: str) -> models.AIRun | None:
    return next((run for run in reversed(record.ai_runs) if run.task == task), None)


def _run_meta(run: models.AIRun) -> dict:
    return {"provider": run.provider, "model": run.model, "error": run.error, "created_at": run.created_at}


def record_out(record: models.Record, user_id: int) -> dict:
    screening = _latest_run(record, "screening")
    extraction = _latest_run(record, "extraction")
    appraisal = _latest_run(record, "appraisal")
    my_decision = next(
        (d.decision for d in record.decisions if d.stage == TITLE_ABSTRACT and d.reviewer_id == user_id), None
    )
    return {
        "id": record.id,
        "title": record.title,
        "authors": record.authors,
        "year": record.year,
        "venue": record.venue,
        "doi": record.doi,
        "abstract": record.abstract,
        "source": record.search_run.source_label,
        "duplicate_of_id": record.duplicate_of_id,
        "ai_screening": screening
        and {
            **_run_meta(screening),
            "decision": screening.screening.decision if screening.screening else None,
            "reasoning": screening.screening.reasoning if screening.screening else None,
            "supporting_quote": screening.screening.supporting_quote if screening.screening else None,
        },
        "my_decision": my_decision,
        "final_decision": final_decision(record),
        "extraction": extraction
        and {**_run_meta(extraction), "values": {v.field.name: v.value for v in extraction.extraction_values}},
        "appraisal": appraisal
        and {
            **_run_meta(appraisal),
            "tool": appraisal.appraisal.tool if appraisal.appraisal else None,
            "judgments": appraisal.appraisal.judgments if appraisal.appraisal else None,
        },
    }


def run_out(run: models.SearchRun) -> dict:
    return {
        "id": run.id,
        "kind": run.kind,
        "database": run.database,
        "source": run.source_label,
        "query": run.query,
        "result_count": run.result_count,
        "executed_at": run.executed_at,
    }


def dedup_key(record: models.Record) -> str:
    """Same DOI, or with no DOI the same normalized title, counts as a duplicate."""
    doi = record.doi.strip().lower()
    if doi:
        return doi
    return "title:" + re.sub(r"[^a-z0-9]+", " ", record.title.lower()).strip()


@router.post("/searches", status_code=status.HTTP_201_CREATED, dependencies=[Depends(ai_rate_limit)])
async def run_search(
    body: SearchRunRequest,
    access: ProjectAccess = Depends(project_access(Permission.RUN_SEARCH)),
    db: Session = Depends(get_db),
):
    strategy = get_in_project(db, models.SearchStrategy, body.strategy_id, access.project.id, "Search strategy")
    try:
        if strategy.database.strip().lower() == "pubmed":
            results = await asyncio.to_thread(search_pubmed, strategy.query, body.limit)
            source_label = "PubMed"
        else:
            # No native connector yet for this database; label the results as coming from OpenAlex.
            results = await asyncio.to_thread(search_openalex, strategy.query, body.limit)
            source_label = f"OpenAlex (no native {strategy.database} connector yet)"
    except SearchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    run = models.SearchRun(
        project_id=access.project.id,
        strategy_id=strategy.id,
        kind="database",
        database=strategy.database,
        source_label=source_label,
        query=strategy.query,
        result_count=len(results),
        executed_by_id=access.user.id,
    )
    run.records = [
        models.Record(
            project_id=access.project.id,
            title=result["title"],
            authors=result["authors"],
            year=str(result["year"]),
            venue=result.get("venue", ""),
            doi=result["doi"],
            external_id=str(result["id"])[:100],
            abstract=result["abstract"],
        )
        for result in results
    ]
    db.add(run)
    db.flush()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="search.run",
        entity_type="search_run",
        entity_id=run.id,
        details={
            "database": strategy.database,
            "source": source_label,
            "query": strategy.query,
            "results": len(results),
        },
    )
    db.commit()
    return run_out(run)


@router.post("/imports", status_code=status.HTTP_201_CREATED)
def import_records(
    body: ImportRequest,
    access: ProjectAccess = Depends(project_access(Permission.RUN_SEARCH)),
    db: Session = Depends(get_db),
):
    run = models.SearchRun(
        project_id=access.project.id,
        kind="import",
        database=body.file_name,
        source_label=f"Manual upload: {body.file_name}",
        result_count=len(body.records),
        executed_by_id=access.user.id,
    )
    run.records = [models.Record(project_id=access.project.id, **record.model_dump()) for record in body.records]
    db.add(run)
    db.flush()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="records.imported",
        entity_type="search_run",
        entity_id=run.id,
        details={"file_name": body.file_name, "records": len(body.records)},
    )
    db.commit()
    return run_out(run)


@router.get("/records")
def list_records(
    include_duplicates: bool = False,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    query = select(models.Record).where(models.Record.project_id == access.project.id).order_by(models.Record.id)
    if not include_duplicates:
        query = query.where(models.Record.duplicate_of_id.is_(None))
    return [record_out(record, access.user.id) for record in db.scalars(with_record_details(query))]


@router.delete("/records", status_code=status.HTTP_204_NO_CONTENT)
def clear_records(
    access: ProjectAccess = Depends(project_access(Permission.EDIT_PROJECT)), db: Session = Depends(get_db)
):
    record_count = db.scalar(
        select(func.count()).select_from(models.Record).where(models.Record.project_id == access.project.id)
    )
    # Records, their AI runs, suggestions, and decisions are removed by ON DELETE CASCADE.
    db.execute(delete(models.SearchRun).where(models.SearchRun.project_id == access.project.id))
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="records.cleared",
        entity_type="project",
        entity_id=access.project.id,
        details={"records_deleted": record_count},
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/deduplicate")
def deduplicate_records(
    access: ProjectAccess = Depends(project_access(Permission.RUN_SEARCH)), db: Session = Depends(get_db)
):
    kept: dict[str, int] = {}
    marked: list[int] = []
    unique_records = db.scalars(
        select(models.Record)
        .where(models.Record.project_id == access.project.id, models.Record.duplicate_of_id.is_(None))
        .order_by(models.Record.id)
    )
    for record in unique_records:
        key = dedup_key(record)
        if key in kept:
            record.duplicate_of_id = kept[key]
            marked.append(record.id)
        else:
            kept[key] = record.id

    if marked:
        record_event(
            db,
            project_id=access.project.id,
            actor_id=access.user.id,
            action="records.deduplicated",
            entity_type="project",
            entity_id=access.project.id,
            details={"duplicate_record_ids": marked},
        )
    db.commit()
    return {"duplicates_marked": len(marked)}


@router.get("/prisma")
def prisma_counts(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    """PRISMA 2020 identification and screening counts, computed only from stored runs, records, and decisions."""
    runs = db.scalars(select(models.SearchRun).where(models.SearchRun.project_id == access.project.id)).all()
    records = db.scalars(
        select(models.Record)
        .where(models.Record.project_id == access.project.id)
        .options(selectinload(models.Record.decisions))
    ).all()
    unique_records = [record for record in records if record.duplicate_of_id is None]
    decisions = [final_decision(record) for record in unique_records]

    by_source: dict[str, int] = {}
    for run in runs:
        by_source[run.source_label] = by_source.get(run.source_label, 0) + run.result_count

    return {
        "identified_from_databases": sum(run.result_count for run in runs if run.kind == "database"),
        "identified_from_uploads": sum(run.result_count for run in runs if run.kind == "import"),
        "by_source": by_source,
        "duplicates_removed": len(records) - len(unique_records),
        "screened": len(unique_records),
        "excluded": decisions.count("exclude"),
        "included": decisions.count("include"),
        "awaiting_decision": sum(1 for decision in decisions if decision in (None, "undecided")),
    }
