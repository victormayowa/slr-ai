"""Full texts for records: upload, open-access retrieval (one record now, or many as a background job), the parsed
passages, file download, reparsing, and deletion."""

import asyncio
from collections import defaultdict
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

import models
from ai_tasks import MAX_RECORDS_PER_JOB
from audit import record_event
from database import get_db
from documents import (
    DuplicateDocument,
    apply_parse,
    parse_content,
    require_documents_open,
    retrieve_for_record,
    store_document,
    unpaywall_email,
)
from jobs import job_out, start_job
from permissions import Permission
from projects_routes import ProjectAccess, get_in_project, project_access
from rate_limiting import ai_rate_limit
from records_routes import record_brief
from review_data import final_decision
from security import MAX_DOCUMENT_BYTES
from storage import StorageError, document_storage

router = APIRouter(prefix="/api/projects/{project_id}", tags=["documents"])


class RetrievalJobRequest(BaseModel):
    # Defaults to every included record without a full text.
    record_ids: list[int] | None = Field(None, min_length=1, max_length=MAX_RECORDS_PER_JOB)


def document_out(document: models.Document, span_count: int | None = None) -> dict:
    return {
        "id": document.id,
        "record_id": document.record_id,
        "role": document.role,
        "origin": document.origin,
        "source_url": document.source_url,
        "file_name": document.file_name,
        "media_type": document.media_type,
        "size_bytes": document.size_bytes,
        "sha256": document.sha256,
        "license": document.license,
        "oa_status": document.oa_status,
        "version": document.version,
        "parse_status": document.parse_status,
        "parse_error": document.parse_error,
        "parser": document.parser,
        "page_count": document.page_count,
        "span_count": span_count,
        "uploaded_by": document.uploaded_by.full_name if document.uploaded_by else None,
        "created_at": document.created_at,
        "parsed_at": document.parsed_at,
    }


def span_out(span: models.DocumentSpan) -> dict:
    return {
        "id": span.id,
        "position": span.position,
        "kind": span.kind,
        "section": span.section,
        "page": span.page,
        "label": span.label,
        "text": span.text,
        "start": span.start_offset,
        "end": span.end_offset,
    }


def retrieval_out(retrieval: models.FullTextRetrieval) -> dict:
    return {
        "id": retrieval.id,
        "record_id": retrieval.record_id,
        "status": retrieval.status,
        "attempts": retrieval.attempts,
        "document_id": retrieval.document_id,
        "requested_by": retrieval.requested_by.full_name if retrieval.requested_by else None,
        "created_at": retrieval.created_at,
    }


def _unique_record(db: Session, access: ProjectAccess, record_id: int) -> models.Record:
    record = get_in_project(db, models.Record, record_id, access.project.id, "Record")
    if record.duplicate_of_id is not None:
        raise HTTPException(
            status_code=400, detail="This record is marked as a duplicate; add full texts to the record it duplicates"
        )
    return record


def _document(db: Session, access: ProjectAccess, document_id: int) -> models.Document:
    return get_in_project(db, models.Document, document_id, access.project.id, "Document")


def _unique_records(db: Session, project_id: int) -> list[models.Record]:
    return list(
        db.scalars(
            select(models.Record)
            .where(models.Record.project_id == project_id, models.Record.duplicate_of_id.is_(None))
            .options(selectinload(models.Record.decisions), selectinload(models.Record.search_run))
            .order_by(models.Record.id)
        )
    )


@router.get("/full-texts")
def list_full_texts(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    """Included records and any other records with documents, each with its documents and latest retrieval."""
    project_id = access.project.id
    documents: dict[int, list[models.Document]] = defaultdict(list)
    for document in db.scalars(
        select(models.Document)
        .where(models.Document.project_id == project_id)
        .options(selectinload(models.Document.uploaded_by))
        .order_by(models.Document.id)
    ):
        documents[document.record_id].append(document)
    span_counts: dict[int, int] = {
        document_id: count
        for document_id, count in db.execute(
            select(models.DocumentSpan.document_id, func.count())
            .join(models.Document)
            .where(models.Document.project_id == project_id)
            .group_by(models.DocumentSpan.document_id)
        ).tuples()
    }
    latest: dict[int, models.FullTextRetrieval] = {}
    for retrieval in db.scalars(
        select(models.FullTextRetrieval)
        .where(models.FullTextRetrieval.project_id == project_id)
        .options(selectinload(models.FullTextRetrieval.requested_by))
        .order_by(models.FullTextRetrieval.id)
    ):
        latest[retrieval.record_id] = retrieval

    rows, sought, retrieved = [], 0, 0
    for record in _unique_records(db, project_id):
        decision = final_decision(record)
        record_documents = documents.get(record.id, [])
        if decision != "include" and not record_documents:
            continue
        if decision == "include":
            sought += 1
            retrieved += any(document.role == "full_text" for document in record_documents)
        rows.append(
            {
                "record": record_brief(record),
                "final_decision": decision,
                "documents": [document_out(d, span_counts.get(d.id, 0)) for d in record_documents],
                "latest_retrieval": retrieval_out(latest[record.id]) if record.id in latest else None,
            }
        )
    return {
        "records": rows,
        "counts": {"sought": sought, "retrieved": retrieved, "not_retrieved": sought - retrieved},
        "max_document_bytes": MAX_DOCUMENT_BYTES,
        "unpaywall_configured": bool(unpaywall_email()),
    }


@router.post("/records/{record_id}/documents", status_code=status.HTTP_201_CREATED)
async def upload_document(
    record_id: int,
    file: UploadFile = File(...),
    role: Literal["full_text", "supplement"] = Form("full_text"),
    access: ProjectAccess = Depends(project_access(Permission.EXTRACT)),
    db: Session = Depends(get_db),
):
    """Store a full text or supplementary file for a record and read it into passages."""
    require_documents_open(db, access.project.id)
    record = _unique_record(db, access, record_id)
    content = await file.read(MAX_DOCUMENT_BYTES + 1)
    if len(content) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail=f"Files can be at most {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB")
    if not content:
        raise HTTPException(status_code=400, detail="The file is empty")
    file_name = (file.filename or "upload").replace("\\", "/").rsplit("/", 1)[-1][:255] or "upload"
    outcome = await asyncio.to_thread(parse_content, file_name, content)
    try:
        document = store_document(
            db,
            document_storage(),
            access,
            record,
            content=content,
            file_name=file_name,
            origin="upload",
            outcome=outcome,
            role=role,
        )
    except DuplicateDocument as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="document.uploaded",
        entity_type="document",
        entity_id=document.id,
        details={
            "record_id": record.id,
            "file_name": file_name,
            "role": role,
            "sha256": document.sha256,
            "size_bytes": document.size_bytes,
            "parse_status": document.parse_status,
        },
    )
    db.commit()
    return document_out(document, len(document.spans))


@router.post("/records/{record_id}/full-text/retrieve", dependencies=[Depends(ai_rate_limit)])
async def retrieve_record_full_text(
    record_id: int,
    access: ProjectAccess = Depends(project_access(Permission.EXTRACT)),
    db: Session = Depends(get_db),
):
    """Look for an open-access full text of one record now (Europe PMC, then Unpaywall)."""
    require_documents_open(db, access.project.id)
    record = _unique_record(db, access, record_id)
    retrieval = await retrieve_for_record(db, access, record, document_storage())
    db.commit()
    document = retrieval.document
    return {
        "retrieval": retrieval_out(retrieval),
        "document": document_out(document, len(document.spans)) if document is not None else None,
    }


@router.post("/full-texts/retrieve", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(ai_rate_limit)])
async def start_retrieval_job(
    body: RetrievalJobRequest,
    access: ProjectAccess = Depends(project_access(Permission.EXTRACT)),
    db: Session = Depends(get_db),
):
    """Queue open-access retrieval for many records. Poll GET /jobs/{job_id} for progress."""
    require_documents_open(db, access.project.id)
    records = _unique_records(db, access.project.id)
    if body.record_ids is None:
        included = [record for record in records if final_decision(record) == "include"]
        if not included:
            raise HTTPException(status_code=400, detail="Include records at screening first")
        with_full_text = set(
            db.scalars(
                select(models.Document.record_id).where(
                    models.Document.project_id == access.project.id, models.Document.role == "full_text"
                )
            )
        )
        record_ids = [record.id for record in included if record.id not in with_full_text][:MAX_RECORDS_PER_JOB]
        if not record_ids:
            raise HTTPException(status_code=400, detail="Every included record already has a full text")
    else:
        known = {record.id for record in records}
        record_ids = list(dict.fromkeys(body.record_ids))
        if any(record_id not in known for record_id in record_ids):
            raise HTTPException(status_code=404, detail="Some records weren't found, or are marked as duplicates")
    job = await start_job(db, access, "fulltext", record_ids)
    return job_out(job)


@router.get("/documents/{document_id}")
def get_document(
    document_id: int,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    document = _document(db, access, document_id)
    return {**document_out(document, len(document.spans)), "spans": [span_out(span) for span in document.spans]}


@router.get("/documents/{document_id}/file")
def download_document(
    document_id: int,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    document = _document(db, access, document_id)
    try:
        content = document_storage().read(document.storage_key)
    except StorageError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content,
        media_type=document.media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(document.file_name)}"},
    )


@router.post("/documents/{document_id}/parse")
async def reparse_document(
    document_id: int,
    access: ProjectAccess = Depends(project_access(Permission.EXTRACT)),
    db: Session = Depends(get_db),
):
    """Read the stored file again, for example after the parsers improve."""
    require_documents_open(db, access.project.id)
    document = _document(db, access, document_id)
    try:
        content = document_storage().read(document.storage_key)
    except StorageError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    outcome = await asyncio.to_thread(parse_content, document.file_name, content)
    apply_parse(db, document, outcome)
    db.flush()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="document.reparsed",
        entity_type="document",
        entity_id=document.id,
        details={"parse_status": document.parse_status, "parser": document.parser, "spans": len(document.spans)},
    )
    db.commit()
    return {**document_out(document, len(document.spans)), "spans": [span_out(span) for span in document.spans]}


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int,
    access: ProjectAccess = Depends(project_access(Permission.EXTRACT)),
    db: Session = Depends(get_db),
):
    require_documents_open(db, access.project.id)
    document = _document(db, access, document_id)
    storage_key = document.storage_key
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="document.deleted",
        entity_type="document",
        entity_id=document.id,
        details={"record_id": document.record_id, "file_name": document.file_name, "sha256": document.sha256},
    )
    db.delete(document)
    db.commit()
    document_storage().delete(storage_key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
