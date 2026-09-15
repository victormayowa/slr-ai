"""Database searches, file imports, the record list, deduplication, and PRISMA counts."""

import asyncio
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql import Select

import models
from audit import record_event
from database import get_db
from dedup import candidate_pairs, find_duplicates, reviewed_pairs
from permissions import Permission
from prisma_flow import flow_counts, legacy_counts, to_csv, to_svg
from projects_routes import ProjectAccess, get_in_project, project_access
from rate_limiting import ai_rate_limit
from review_data import FULL_TEXT, SCREENING_STAGES, TITLE_ABSTRACT, ReviewPolicy, latest_run
from review_settings import load_settings
from search_sources import CONNECTORS, MAX_RESULTS, catalog, connector_for, import_only_source
from services.errors import SearchError
from services.record_import import ImportFormatError, parse_records
from storage import document_storage
from workflow import require_stage_open

router = APIRouter(prefix="/api/projects/{project_id}", tags=["records"])

MAX_IMPORT_RECORDS = 5000


class SearchRunRequest(BaseModel):
    strategy_id: int
    limit: int = Field(200, ge=1, le=MAX_RESULTS)
    # Run the strategy's search string on this connector instead of the one matching its database.
    connector: str | None = None


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
        selectinload(models.Record.adjudications),
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


def _suggestion_out(run: models.AIRun | None) -> dict | None:
    if run is None:
        return None
    suggestion = run.screening
    return {
        **_run_meta(run),
        "decision": suggestion.decision if suggestion else None,
        "reasoning": suggestion.reasoning if suggestion else None,
        "supporting_quote": suggestion.supporting_quote if suggestion else None,
        "quote_verified": suggestion.quote_verified if suggestion else None,
        "confidence": suggestion.confidence if suggestion else None,
        "criteria_judgments": suggestion.criteria_judgments if suggestion else [],
        "document_id": suggestion.document_id if suggestion else None,
        "supporting_span_id": suggestion.supporting_span_id if suggestion else None,
    }


def screening_view(record: models.Record, user_id: int, policy: ReviewPolicy, blind: bool) -> dict[str, dict]:
    """Each screening stage's state for this reviewer. With blinded dual screening, the state, final decision, and AI
    suggestion stay hidden until the reviewer records their own decision."""
    view = {}
    for stage in SCREENING_STAGES:
        mine = next((d for d in record.decisions if d.stage == stage and d.reviewer_id == user_id), None)
        status = policy.status(record, stage)
        hidden = (
            blind
            and policy.reviewers(stage) > 1
            and (mine is None or mine.decision == "undecided")
            and status.state != "adjudicated"
        )
        view[stage] = {
            "state": "hidden" if hidden else status.state,
            "final_decision": None if hidden else status.final,
            "reason_code": None if hidden else status.reason_code,
            "my_decision": mine.decision if mine else None,
            "my_reason_code": mine.reason_code if mine else None,
            "my_note": mine.note if mine else None,
            "reviewers_decided": status.reviewers_decided,
            "reviewers_required": policy.reviewers(stage),
        }
    return view


def record_out(record: models.Record, user_id: int, policy: ReviewPolicy | None = None, blind: bool = False) -> dict:
    screening = latest_run(record, "screening")
    extraction = latest_run(record, "extraction")
    appraisal = latest_run(record, "appraisal")
    view = screening_view(record, user_id, policy or ReviewPolicy(), blind)
    title_abstract, full_text = view[TITLE_ABSTRACT], view[FULL_TEXT]
    return {
        "screening": view,
        "ai_screening_hidden": title_abstract["state"] == "hidden" and screening is not None,
        "ai_full_text_screening": None
        if full_text["state"] == "hidden"
        else _suggestion_out(latest_run(record, "fulltext_screening")),
        "id": record.id,
        "title": record.title,
        "authors": record.authors,
        "year": record.year,
        "venue": record.venue,
        "doi": record.doi,
        "abstract": record.abstract,
        "identifiers": record.identifiers or {},
        "url": record.url,
        "source": record.search_run.source_label,
        "duplicate_of_id": record.duplicate_of_id,
        "ai_screening": None if title_abstract["state"] == "hidden" else _suggestion_out(screening),
        "my_decision": title_abstract["my_decision"],
        "final_decision": title_abstract["final_decision"],
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
        "total_available": run.total_available,
        "connector": run.connector,
        "strategy_version": run.strategy_version,
        "interface": run.interface,
        "searched_on": run.searched_on,
        "file_format": run.file_format,
        "filters": run.filters,
        "executed_at": run.executed_at,
    }


def new_record(project_id: int, result: dict) -> models.Record:
    return models.Record(
        project_id=project_id,
        title=str(result.get("title") or "No Title")[:2000],
        authors=str(result.get("authors") or "")[:5000],
        year=str(result.get("year") or "")[:20],
        venue=str(result.get("venue") or "")[:1000],
        doi=str(result.get("doi") or "")[:255],
        external_id=str(result.get("id") or "")[:100],
        abstract=str(result.get("abstract") or ""),
        identifiers=result.get("identifiers") or {},
        url=str(result.get("url") or "")[:1000],
    )


@router.get("/search-sources", include_in_schema=True)
def search_sources(access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT))):
    """Connectors that can run searches, and databases searched on their own platform and imported."""
    return catalog()


@router.get("/search-runs")
def list_search_runs(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    runs = db.scalars(
        select(models.SearchRun).where(models.SearchRun.project_id == access.project.id).order_by(models.SearchRun.id)
    )
    return [run_out(run) for run in runs]


@router.post("/searches", status_code=status.HTTP_201_CREATED, dependencies=[Depends(ai_rate_limit)])
async def run_search(
    body: SearchRunRequest,
    access: ProjectAccess = Depends(project_access(Permission.RUN_SEARCH)),
    db: Session = Depends(get_db),
):
    """Run a strategy through a search connector, storing every retrieved record and the run's PRISMA-S details."""
    require_stage_open(db, access.project.id, "search")
    strategy = get_in_project(db, models.SearchStrategy, body.strategy_id, access.project.id, "Search strategy")
    matching = connector_for(strategy.database)
    if body.connector:
        connector = CONNECTORS.get(body.connector)
        if connector is None:
            raise HTTPException(status_code=422, detail=f"Unknown search connector: {body.connector}")
    else:
        connector = matching
    if connector is None:
        source = import_only_source(strategy.database)
        if source is not None:
            detail = (
                f"{source.label} can't be searched from OmniReview. Run the strategy on {source.interface}, export "
                f"the results as {source.export_hint}, and import the file."
            )
        else:
            detail = (
                f"There's no search connector for {strategy.database}. Choose a connector to run this search string "
                "on, or import an export file."
            )
        raise HTTPException(status_code=400, detail=detail)
    try:
        results, total = await asyncio.to_thread(connector.search, strategy.query, body.limit)
    except SearchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    source_label = (
        connector.label
        if matching is not None and matching.key == connector.key
        else f"{connector.label} (search string written for {strategy.database})"
    )
    run = models.SearchRun(
        project_id=access.project.id,
        strategy_id=strategy.id,
        strategy_version=strategy.version,
        kind=connector.kind,
        database=connector.label,
        source_label=source_label,
        connector=connector.key,
        interface=connector.interface,
        query=strategy.query,
        result_count=len(results),
        total_available=total,
        searched_on=datetime.now(UTC).date().isoformat(),
        filters={"retrieval_limit": body.limit},
        executed_by_id=access.user.id,
    )
    run.records = [new_record(access.project.id, result) for result in results]
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
            "connector": connector.key,
            "database": strategy.database,
            "source": source_label,
            "query": strategy.query,
            "retrieved": len(results),
            "total_available": total,
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


@router.post("/imports/file", status_code=status.HTTP_201_CREATED)
async def import_file(
    file: UploadFile = File(...),
    database: str = Form(..., min_length=1, max_length=200),
    source_type: Literal["database", "register", "other"] = Form("database"),
    interface: str = Form("", max_length=200),
    searched_on: str = Form("", max_length=40),
    query: str = Form("", max_length=20_000),
    access: ProjectAccess = Depends(project_access(Permission.RUN_SEARCH)),
    db: Session = Depends(get_db),
):
    """Import an export file (RIS, MEDLINE, BibTeX, EndNote XML, Web of Science, or CSV) with its PRISMA-S details."""
    require_stage_open(db, access.project.id, "search")
    file_name = (file.filename or "upload")[:200]
    content = await file.read()
    try:
        parsed = await asyncio.to_thread(parse_records, file_name, content)
    except ImportFormatError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not parsed.records:
        raise HTTPException(status_code=400, detail=f"No records with a title were found in {file_name}")
    if len(parsed.records) > MAX_IMPORT_RECORDS:
        raise HTTPException(
            status_code=400, detail=f"{file_name} has more than {MAX_IMPORT_RECORDS} records; split the export"
        )

    run = models.SearchRun(
        project_id=access.project.id,
        kind=source_type,
        database=database.strip(),
        source_label=f"{database.strip()} export ({file_name})",
        interface=interface.strip() or None,
        query=query.strip() or None,
        searched_on=searched_on.strip() or None,
        file_format=parsed.format,
        result_count=len(parsed.records),
        filters={"file_name": file_name, "entries_without_title": parsed.skipped},
        executed_by_id=access.user.id,
    )
    run.records = [models.Record(project_id=access.project.id, **record) for record in parsed.records]
    db.add(run)
    db.flush()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="records.imported",
        entity_type="search_run",
        entity_id=run.id,
        details={
            "file_name": file_name,
            "format": parsed.format,
            "database": run.database,
            "records": len(parsed.records),
            "entries_without_title": parsed.skipped,
        },
    )
    db.commit()
    return {**run_out(run), "skipped": parsed.skipped}


@router.get("/records")
def list_records(
    include_duplicates: bool = False,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    query = select(models.Record).where(models.Record.project_id == access.project.id).order_by(models.Record.id)
    if not include_duplicates:
        query = query.where(models.Record.duplicate_of_id.is_(None))
    settings = load_settings(db, access.project.id)
    policy = ReviewPolicy(settings.screening.title_abstract_reviewers, settings.screening.full_text_reviewers)
    blind = settings.screening.blind_dual_screening
    return [record_out(record, access.user.id, policy, blind) for record in db.scalars(with_record_details(query))]


@router.delete("/records", status_code=status.HTTP_204_NO_CONTENT)
def clear_records(
    access: ProjectAccess = Depends(project_access(Permission.EDIT_PROJECT)), db: Session = Depends(get_db)
):
    require_stage_open(db, access.project.id, "search")
    record_count = db.scalar(
        select(func.count()).select_from(models.Record).where(models.Record.project_id == access.project.id)
    )
    storage_keys = list(
        db.scalars(select(models.Document.storage_key).where(models.Document.project_id == access.project.id))
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
        details={"records_deleted": record_count, "documents_deleted": len(storage_keys)},
    )
    db.commit()
    storage = document_storage()
    for key in storage_keys:
        storage.delete(key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/deduplicate")
def deduplicate_records(
    access: ProjectAccess = Depends(project_access(Permission.RUN_SEARCH)), db: Session = Depends(get_db)
):
    """Mark records that share an identifier, or a title and year, and report pairs a reviewer needs to check."""
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
    possible = candidate_pairs(records, reviewed_pairs(db, access.project.id))
    return {"duplicates_marked": len(marked), "possible_duplicates": len(possible)}


@router.get("/prisma")
def prisma_counts(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    """PRISMA 2020 counts, computed only from stored runs, records, decisions, documents, and studies."""
    return legacy_counts(flow_counts(db, access.project.id))


@router.get("/prisma/flow")
def prisma_flow(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    """The PRISMA 2020 flow diagram's counts, with databases and registers and other methods in separate columns."""
    return flow_counts(db, access.project.id)


@router.get("/prisma/flow.svg")
def prisma_flow_svg(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    return Response(
        to_svg(flow_counts(db, access.project.id)),
        media_type="image/svg+xml",
        headers={"Content-Disposition": 'attachment; filename="prisma-2020-flow.svg"'},
    )


@router.get("/prisma/flow.csv")
def prisma_flow_csv(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    """The counts in the layout of the PRISMA2020 R package's data template, for its flow diagram tool."""
    return Response(
        to_csv(flow_counts(db, access.project.id)),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="prisma-2020-flow.csv"'},
    )


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


def record_brief(record: models.Record) -> dict:
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
                "record": record_brief(records[row.record_id]),
                "other": record_brief(records[row.other_id]),
                "similarity": round(float(row.similarity), 4),
            }
            for row in rows
        ],
    }


def _mark_as_duplicate(db: Session, record: models.Record, original: models.Record) -> list[int]:
    """Point the record at its original, moving records that duplicated it along. Returns the ids moved."""
    if original.id == record.id:
        raise HTTPException(status_code=422, detail="A record can't be a duplicate of itself")
    if original.duplicate_of_id is not None:
        raise HTTPException(
            status_code=409, detail="That record is itself marked as a duplicate. Choose the record it duplicates."
        )
    children = db.scalars(select(models.Record).where(models.Record.duplicate_of_id == record.id)).all()
    for child in children:
        child.duplicate_of_id = original.id
    record.duplicate_of_id = original.id
    return [child.id for child in children]


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
    previous = record.duplicate_of_id
    moved: list[int] = []
    if body.duplicate_of_id is not None:
        original = get_in_project(db, models.Record, body.duplicate_of_id, access.project.id, "Record")
        moved = _mark_as_duplicate(db, record, original)
    else:
        record.duplicate_of_id = None
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


class CandidateDecision(BaseModel):
    record_id: int
    other_record_id: int
    decision: Literal["duplicate", "not_duplicate"]
    # Required for "duplicate": which of the two records to keep.
    keep_record_id: int | None = None


@router.get("/duplicate-candidates")
def duplicate_candidates(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    """Pairs of records with very similar titles and close years that a reviewer hasn't decided on."""
    records = db.scalars(
        select(models.Record)
        .where(models.Record.project_id == access.project.id)
        .options(selectinload(models.Record.search_run))
    ).all()
    by_id = {record.id: record for record in records}
    pairs = candidate_pairs(records, reviewed_pairs(db, access.project.id))
    return {
        "pairs": [
            {
                "record": record_brief(by_id[pair.record_id]),
                "other": record_brief(by_id[pair.other_id]),
                "score": pair.score,
                "reasons": pair.reasons,
            }
            for pair in pairs
        ]
    }


@router.post("/duplicate-candidates/decision")
def decide_duplicate_candidate(
    body: CandidateDecision,
    access: ProjectAccess = Depends(project_access(Permission.RUN_SEARCH)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "search")
    if body.record_id == body.other_record_id:
        raise HTTPException(status_code=422, detail="Choose two different records")
    record = get_in_project(db, models.Record, body.record_id, access.project.id, "Record")
    other = get_in_project(db, models.Record, body.other_record_id, access.project.id, "Record")
    first_id, second_id = sorted((record.id, other.id))
    details: dict = {"record_id": first_id, "other_record_id": second_id, "decision": body.decision}
    if body.decision == "duplicate":
        if body.keep_record_id not in (record.id, other.id):
            raise HTTPException(status_code=422, detail="Choose which of the two records to keep")
        kept, duplicate = (record, other) if body.keep_record_id == record.id else (other, record)
        if kept.duplicate_of_id is not None or duplicate.duplicate_of_id is not None:
            raise HTTPException(status_code=409, detail="One of these records is already marked as a duplicate")
        details["kept_record_id"] = kept.id
        details["also_moved"] = _mark_as_duplicate(db, duplicate, kept)

    review = db.scalar(
        select(models.DuplicateReview).where(
            models.DuplicateReview.project_id == access.project.id,
            models.DuplicateReview.record_id == first_id,
            models.DuplicateReview.other_record_id == second_id,
        )
    ) or models.DuplicateReview(project_id=access.project.id, record_id=first_id, other_record_id=second_id)
    review.decision, review.reviewer_id, review.created_at = body.decision, access.user.id, models.utcnow()
    db.add(review)
    db.flush()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="duplicates.reviewed",
        entity_type="record",
        entity_id=first_id,
        details=details,
    )
    db.commit()
    return {
        "decision": body.decision,
        "records": [record_out(record, access.user.id), record_out(other, access.user.id)],
    }
