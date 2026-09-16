"""Full-text documents: storing files, parsing them into spans, and retrieving open-access copies for records.

Documents stay private to their project. A file that can't be read yet (for example a scanned PDF) is still kept, with
the reason stored on the document, so it can be reparsed when better parsers are added.
"""

import asyncio
import hashlib
import logging
import os
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

import entitlements
import models
from audit import record_event
from document_parsing import (
    MEDIA_TYPES,
    FileKind,
    ParsedDocument,
    ParseError,
    detect_kind,
    parse_document,
    span_offsets,
)
from llm.grounding import Passage
from projects_routes import ProjectAccess
from security import MAX_DOCUMENT_BYTES
from services.fulltext import find_full_text, record_ids
from storage import DocumentStorage, document_storage
from workflow import WorkflowError, require_stage_open

logger = logging.getLogger(__name__)

DOCUMENT_ROLES = ("full_text", "supplement")


class DuplicateDocument(Exception):
    def __init__(self, document: models.Document) -> None:
        super().__init__("This file is already stored for the record")
        self.document = document


def require_documents_open(db: Session, project_id: int) -> None:
    """Full texts are gathered during screening and used through extraction, so one of those stages must be open."""
    for stage in ("screening", "full_text_screening", "extraction"):
        try:
            require_stage_open(db, project_id, stage)
            return
        except WorkflowError:
            continue
    raise WorkflowError(
        "Full texts can be added or changed while screening, full-text screening, or extraction is open."
    )


# Parsed full texts are preferred in this order: publisher XML keeps the structure best.
_PARSER_PREFERENCE = ("jats", "docx", "pypdf", "text")


def best_full_texts(db: Session, record_ids: list[int]) -> dict[int, models.Document]:
    """Each record's best parsed full text, if it has one."""
    documents = db.scalars(
        select(models.Document)
        .where(
            models.Document.record_id.in_(record_ids),
            models.Document.role == "full_text",
            models.Document.parse_status == "parsed",
        )
        .order_by(models.Document.id.desc())
    ).all()
    best: dict[int, models.Document] = {}

    def rank(document: models.Document) -> int:
        return next((i for i, prefix in enumerate(_PARSER_PREFERENCE) if document.parser.startswith(prefix)), 9)

    for document in documents:
        current = best.get(document.record_id)
        if current is None or rank(document) < rank(current):
            best[document.record_id] = document
    return best


def document_passages(db: Session, document_ids: list[int]) -> dict[int, list[Passage]]:
    passages: dict[int, list[Passage]] = {document_id: [] for document_id in document_ids}
    for span in db.scalars(
        select(models.DocumentSpan)
        .where(models.DocumentSpan.document_id.in_(document_ids))
        .order_by(models.DocumentSpan.document_id, models.DocumentSpan.position)
    ):
        passages[span.document_id].append(Passage(span.id, span.text, span.kind, span.section, span.page, span.label))
    return passages


def unpaywall_email() -> str:
    return (os.getenv("UNPAYWALL_EMAIL") or os.getenv("CROSSREF_EMAIL") or os.getenv("OPENALEX_EMAIL") or "").strip()


@dataclass
class ParseOutcome:
    kind: FileKind
    parsed: ParsedDocument | None
    error: str | None


def parse_content(file_name: str, content: bytes) -> ParseOutcome:
    """Read a file into spans. It doesn't touch the database, so callers run it in a worker thread."""
    kind = detect_kind(file_name, content)
    try:
        return ParseOutcome(kind, parse_document(content, kind), None)
    except ParseError as exc:
        return ParseOutcome(kind, None, str(exc))
    except Exception:
        logger.exception("Unexpected error while parsing a %s document", kind)
        return ParseOutcome(kind, None, "The file couldn't be read")


def apply_parse(db: Session, document: models.Document, outcome: ParseOutcome) -> None:
    """Replace the document's spans with the parse outcome."""
    document.spans.clear()
    db.flush()
    document.parsed_at = models.utcnow()
    parsed = outcome.parsed
    if parsed is None:
        document.parse_status = "unsupported" if outcome.kind == "unsupported" else "failed"
        document.parse_error, document.parser, document.page_count = outcome.error, "", None
        return
    offsets = span_offsets(span.text for span in parsed.spans)
    document.spans = [
        models.DocumentSpan(
            position=position,
            kind=span.kind,
            section=span.section[:500],
            page=span.page,
            label=span.label[:100],
            text=span.text,
            start_offset=start,
            end_offset=end,
        )
        for position, (span, (start, end)) in enumerate(zip(parsed.spans, offsets, strict=True))
    ]
    document.parse_status, document.parse_error = "parsed", None
    document.parser, document.page_count = parsed.parser, parsed.page_count


def store_document(
    db: Session,
    storage: DocumentStorage,
    access: ProjectAccess,
    record: models.Record,
    *,
    content: bytes,
    file_name: str,
    origin: str,
    outcome: ParseOutcome,
    role: str = "full_text",
    source_url: str = "",
    license: str = "",
    oa_status: str = "",
    version: str = "",
) -> models.Document:
    """Save the file and its spans. Raises DuplicateDocument when the record already has an identical file."""
    digest = hashlib.sha256(content).hexdigest()
    existing = db.scalar(
        select(models.Document).where(models.Document.record_id == record.id, models.Document.sha256 == digest)
    )
    if existing is not None:
        raise DuplicateDocument(existing)
    entitlements.require(
        db, entitlements.account_for_project(access.project), "storage_mb", round(len(content) / (1024 * 1024), 3)
    )
    key = storage.save(access.project.id, content)
    try:
        document = models.Document(
            project_id=access.project.id,
            record_id=record.id,
            role=role,
            origin=origin,
            source_url=source_url[:1000],
            file_name=file_name[:255],
            media_type=MEDIA_TYPES[outcome.kind],
            size_bytes=len(content),
            sha256=digest,
            storage_key=key,
            license=license[:100],
            oa_status=oa_status[:20],
            version=version[:40],
            parse_status="pending",
            uploaded_by_id=access.user.id,
        )
        db.add(document)
        db.flush()
        apply_parse(db, document, outcome)
        db.flush()
    except Exception:
        storage.delete(key)
        raise
    return document


async def retrieve_for_record(
    db: Session, access: ProjectAccess, record: models.Record, storage: DocumentStorage
) -> models.FullTextRetrieval:
    """Look for an open-access full text of the record, store it if found, and record every attempt."""
    ids = record_ids(record.doi, record.identifiers)
    result = await asyncio.to_thread(find_full_text, ids, unpaywall_email(), MAX_DOCUMENT_BYTES)
    retrieval = models.FullTextRetrieval(
        project_id=access.project.id,
        record_id=record.id,
        status="not_found",
        attempts=[asdict(attempt) for attempt in result.attempts],
        requested_by_id=access.user.id,
    )
    db.add(retrieval)
    fetched = result.file
    if fetched is not None:
        outcome = await asyncio.to_thread(parse_content, fetched.file_name, fetched.content)
        try:
            document = store_document(
                db,
                storage,
                access,
                record,
                content=fetched.content,
                file_name=fetched.file_name,
                origin=fetched.origin,
                outcome=outcome,
                source_url=fetched.source_url,
                license=fetched.license,
                oa_status=fetched.oa_status,
                version=fetched.version,
            )
        except DuplicateDocument as duplicate:
            retrieval.status, retrieval.document_id = "already_stored", duplicate.document.id
        else:
            retrieval.status, retrieval.document = "found", document
            db.flush()
            record_event(
                db,
                project_id=access.project.id,
                actor_id=access.user.id,
                action="document.retrieved",
                entity_type="document",
                entity_id=document.id,
                details={
                    "record_id": record.id,
                    "origin": document.origin,
                    "source_url": document.source_url,
                    "sha256": document.sha256,
                    "license": document.license,
                    "parse_status": document.parse_status,
                },
            )
    db.flush()
    return retrieval


async def retrieve_full_texts(
    db: Session, access: ProjectAccess, job: models.AIJob, records: list[models.Record]
) -> None:
    """Background job: look for full texts of each record in turn, committing after each one."""
    storage = document_storage()
    by_status: dict[str, list[int]] = {"found": [], "already_stored": [], "not_found": []}
    for record in records:
        record_id = record.id
        try:
            retrieval = await retrieve_for_record(db, access, record, storage)
        except Exception:
            logger.exception("Full-text retrieval failed for record %s", record_id)
            db.rollback()
            job.failed += 1
            job.error = job.error or "Unexpected server error while retrieving some full texts"
        else:
            by_status[retrieval.status].append(record_id)
        job.processed += 1
        db.commit()

    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="fulltext.retrieval_job",
        entity_type="ai_job",
        entity_id=job.id,
        details={**by_status, "failed": job.failed},
    )
    db.commit()
