"""Database searches, file imports, the record list, deduplication, and PRISMA counts."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql import Select

import models
from audit import record_event
from database import get_db
from permissions import Permission
from projects_routes import ProjectAccess, get_in_project, project_access
from rate_limiting import ai_rate_limit
from review_data import TITLE_ABSTRACT, final_decision, find_duplicates, latest_run
from services.errors import SearchError
from services.openalex import search_openalex
from services.pubmed import search_pubmed
from workflow import require_stage_open

router = APIRouter(prefix="/api/projects/{project_id}", tags=["records"])

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


def _run_meta(run: models.AIRun) -> dict:
    return {
        "provider": run.provider,
        "model": run.model,
        "key_source": run.key_source,
        "error": run.error,
        "created_at": run.created_at,
    }


def record_out(record: models.Record, user_id: int) -> dict:
    screening = latest_run(record, "screening")
    extraction = latest_run(record, "extraction")
    appraisal = latest_run(record, "appraisal")
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
            "quote_verified": screening.screening.quote_verified if screening.screening else None,
        },
        "my_decision": my_decision,
        "final_decision": final_decision(record),
        "extraction": extraction
        and {
            **_run_meta(extraction),
            "values": {v.field.name: v.value for v in extraction.extraction_values},
            "evidence": {
                v.field.name: {"quote": v.evidence_quote, "verified": v.quote_verified}
                for v in extraction.extraction_values
            },
        },
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


@router.post("/searches", status_code=status.HTTP_201_CREATED, dependencies=[Depends(ai_rate_limit)])
async def run_search(
    body: SearchRunRequest,
    access: ProjectAccess = Depends(project_access(Permission.RUN_SEARCH)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "search")
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
    require_stage_open(db, access.project.id, "search")
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
    require_stage_open(db, access.project.id, "search")
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
    require_stage_open(db, access.project.id, "search")
    records = db.scalars(select(models.Record).where(models.Record.project_id == access.project.id)).all()
    duplicates = find_duplicates(records)
    for record in records:
        if record.id in duplicates:
            record.duplicate_of_id = duplicates[record.id]
    marked = sorted(duplicates)

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


class DuplicateUpdate(BaseModel):
    duplicate_of_id: int | None


# For each unique record, its nearest unique neighbours by cosine distance, using one embedding model. Each pair is
# kept once (lower record id first).
_SIMILAR_PAIRS_SQL = text(
    """
    SELECT a.record_id AS record_id, near.record_id AS other_id, 1 - near.distance AS similarity
    FROM record_embeddings AS a
    JOIN records AS ra ON ra.id = a.record_id AND ra.duplicate_of_id IS NULL
    CROSS JOIN LATERAL (
        SELECT b.record_id, b.embedding <=> a.embedding AS distance
        FROM record_embeddings AS b
        JOIN records AS rb ON rb.id = b.record_id AND rb.duplicate_of_id IS NULL
        WHERE b.project_id = :project_id AND b.model = :model AND b.record_id <> a.record_id
        ORDER BY b.embedding <=> a.embedding
        LIMIT 5
    ) AS near
    WHERE a.project_id = :project_id AND a.model = :model AND a.record_id < near.record_id
      AND 1 - near.distance >= :min_similarity
    ORDER BY similarity DESC, a.record_id, near.record_id
    LIMIT :limit
    """
)


def _record_brief(record: models.Record) -> dict:
    return {
        "id": record.id,
        "title": record.title,
        "authors": record.authors,
        "year": record.year,
        "doi": record.doi,
        "source": record.search_run.source_label,
    }


@router.get("/similar-pairs")
def similar_record_pairs(
    min_similarity: float = Query(0.9, ge=0.5, le=1.0),
    limit: int = Query(50, ge=1, le=200),
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    """Pairs of unique records with similar meaning, for a reviewer to check as possible duplicates.

    Uses embeddings from the project's embedding model (POST /embeddings creates them). Each record is compared with its
    nearest neighbours, so this is an aid for reviewers rather than an exhaustive comparison.
    """
    model = access.project.embedding_model
    if model is None:
        raise HTTPException(status_code=409, detail="Choose an embedding model for this project first")
    model_name = f"{model.provider}/{model.model_id}"
    rows = db.execute(
        _SIMILAR_PAIRS_SQL,
        {"project_id": access.project.id, "model": model_name, "min_similarity": min_similarity, "limit": limit},
    ).all()
    ids = {row.record_id for row in rows} | {row.other_id for row in rows}
    records = {
        record.id: record
        for record in db.scalars(
            select(models.Record).where(models.Record.id.in_(ids)).options(selectinload(models.Record.search_run))
        )
    }
    embedded = db.scalar(
        select(func.count())
        .select_from(models.RecordEmbedding)
        .join(models.Record, models.Record.id == models.RecordEmbedding.record_id)
        .where(
            models.RecordEmbedding.project_id == access.project.id,
            models.RecordEmbedding.model == model_name,
            models.Record.duplicate_of_id.is_(None),
        )
    )
    unique = db.scalar(
        select(func.count())
        .select_from(models.Record)
        .where(models.Record.project_id == access.project.id, models.Record.duplicate_of_id.is_(None))
    )
    return {
        "model": model_name,
        "embedded_records": embedded or 0,
        "unique_records": unique or 0,
        "pairs": [
            {
                "record": _record_brief(records[row.record_id]),
                "other": _record_brief(records[row.other_id]),
                "similarity": round(float(row.similarity), 4),
            }
            for row in rows
        ],
    }


@router.put("/records/{record_id}/duplicate-of")
def mark_duplicate(
    record_id: int,
    body: DuplicateUpdate,
    access: ProjectAccess = Depends(project_access(Permission.RUN_SEARCH)),
    db: Session = Depends(get_db),
):
    """Mark a record as a duplicate of another, or clear the mark, for duplicates the automatic check can't detect."""
    require_stage_open(db, access.project.id, "search")
    record = get_in_project(db, models.Record, record_id, access.project.id, "Record")
    moved: list[int] = []
    if body.duplicate_of_id is not None:
        if body.duplicate_of_id == record.id:
            raise HTTPException(status_code=422, detail="A record can't be a duplicate of itself")
        original = get_in_project(db, models.Record, body.duplicate_of_id, access.project.id, "Record")
        if original.duplicate_of_id is not None:
            raise HTTPException(
                status_code=409, detail="That record is itself marked as a duplicate. Choose the record it duplicates."
            )
        # Records already marked as duplicates of this one now point at the record it duplicates.
        children = db.scalars(select(models.Record).where(models.Record.duplicate_of_id == record.id)).all()
        for child in children:
            child.duplicate_of_id = original.id
        moved = [child.id for child in children]

    previous = record.duplicate_of_id
    record.duplicate_of_id = body.duplicate_of_id
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="record.duplicate_marked" if body.duplicate_of_id is not None else "record.duplicate_cleared",
        entity_type="record",
        entity_id=record.id,
        details={"duplicate_of_id": body.duplicate_of_id, "previous": previous, "also_moved": moved},
    )
    db.commit()
    return record_out(record, access.user.id)
