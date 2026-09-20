"""Publication: journal finder, author guidelines, submission packages with readiness checks and corresponding-author
confirmation (OmniReview never submits), repository deposits, and peer review responses."""

import asyncio
import html
import io
import ipaddress
import re
import socket
from typing import Literal
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from ai_access import new_ai_run, project_ai, record_usage
from audit import record_event
from certainty_routes import summary_of_findings
from database import get_db
from document_parsing import ParseError, detect_kind, parse_document
from docx_style import styled_document
from extraction_data import included_records
from llm.prompts import COVER_LETTER_PROMPT, GUIDELINE_PROMPT, RESPONSE_LETTER_PROMPT, REVIEW_COMMENTS_PROMPT
from manuscript_state import approval_state, get_manuscript, latest_version, version_content
from permissions import Permission
from projects_routes import ProjectAccess, get_in_project, project_access
from publication_state import (
    STATEMENT_LABELS,
    blocking,
    build_submission_files,
    fair_package,
    manifest,
    readiness,
    stage_completed,
    statements,
    zipped,
)
from rate_limiting import ai_rate_limit
from review_settings import review_policy
from services.ai_publication import (
    REQUIREMENT_NAMES,
    draft_cover_letter,
    draft_response_letter,
    grounded_comments,
    grounded_requirements,
    read_guideline,
    rule_split_comments,
    split_comments,
)
from services.errors import LLMError, SearchError
from services.journals import doaj_journal, medline_indexed, sources_by_ids, sources_for_dois, topic_sources
from services.repositories import (
    RepositoryError,
    figshare_deposit,
    github_deposit,
    gitlab_deposit,
    osf_deposit,
    zenodo_deposit,
    zenodo_metadata,
)
from storage import document_storage
from workflow import STAGE_LABELS, STAGE_PERMISSIONS, STAGES, WorkflowError, reopen_stage, require_stage_open

router = APIRouter(prefix="/api/projects/{project_id}", tags=["publication"])

MAX_GUIDELINE_BYTES = 10 * 1024 * 1024
MAX_JOURNAL_CHECKS = 15
PACKAGED_TARGETS = {
    "dryad": (
        "Dryad has no open deposit API. Sign in at datadryad.org, start a submission, and upload these files; "
        "Dryad curates and assigns a DOI."
    ),
    "medrxiv": (
        "medRxiv has no submission API. Submit the manuscript PDF at submit.medrxiv.org, with the declarations "
        "in statements.md."
    ),
    "osf_preprints": (
        "Create the preprint at osf.io/preprints from these files; OSF preprints are moderated and public once "
        "accepted."
    ),
}


def _audit(db: Session, access: ProjectAccess, action: str, entity_type: str, entity_id: int, details: dict) -> None:
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
    )


def _manuscript(db: Session, access: ProjectAccess) -> models.Manuscript:
    manuscript = get_manuscript(db, access.project.id)
    if manuscript is None:
        raise HTTPException(status_code=409, detail="Write and approve the manuscript first")
    return manuscript


async def _ai_call(db: Session, access: ProjectAccess, prompt, call):  # type: ignore[no-untyped-def]
    ai = project_ai(db, access)
    run = new_ai_run(access, "publication", prompt, ai)
    try:
        result = await call(ai)
    except LLMError as exc:
        run.status, run.error = "failed", str(exc)
        record_usage(run, ai, exc.usage)
        db.add(run)
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    run.status = "succeeded"
    record_usage(run, ai, result.usage)
    db.add(run)
    db.flush()
    return run, result.value


# --- Journals ---


def candidate_out(c: models.JournalCandidate) -> dict:
    return {
        "id": c.id,
        "openalex_id": c.openalex_id,
        "name": c.name,
        "issns": c.issns,
        "publisher": c.publisher,
        "homepage": c.homepage,
        "is_oa": c.is_oa,
        "in_doaj": c.in_doaj,
        "apc_usd": c.apc_usd,
        "h_index": c.h_index,
        "mean_citedness": c.mean_citedness,
        "works_count": c.works_count,
        "medline_indexed": c.medline_indexed,
        "topic_works": c.topic_works,
        "included_study_reports": c.included_study_reports,
        "score": c.score,
        "reasons": c.reasons,
        "warnings": c.warnings,
        "shortlisted": c.shortlisted,
    }


class JournalSearch(BaseModel):
    query: str = Field("", max_length=500)


@router.post("/journals/find")
async def find_journals(
    body: JournalSearch,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_MANUSCRIPT)),
    db: Session = Depends(get_db),
):
    """Journals publishing on the topic and where the included studies appeared, with open access, fees, DOAJ, MEDLINE,
    and metrics. Warnings are heuristics (check Think. Check. Submit.), not verdicts."""
    project = access.project
    manuscript = get_manuscript(db, project.id)
    query = (
        body.query.strip()
        or (project.protocol.question if project.protocol and project.protocol.question else "")
        or (manuscript.title if manuscript else project.title)
    )
    dois = [r.doi for r in included_records(db, project.id, review_policy(db, project.id)) if r.doi]
    try:
        topic = await asyncio.to_thread(topic_sources, query)
        venues = await asyncio.to_thread(sources_for_dois, dois) if dois else {}
        ids = list(dict.fromkeys([sid for sid, _ in topic] + list(venues)))
        sources = await asyncio.to_thread(sources_by_ids, ids) if ids else []
    except SearchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    topic_counts = dict(topic)
    existing = {
        c.openalex_id: c
        for c in db.scalars(select(models.JournalCandidate).where(models.JournalCandidate.project_id == project.id))
    }
    import math

    ranked = []
    for source in sources:
        sid = source["id"].rstrip("/").split("/")[-1]
        stats = source.get("summary_stats") or {}
        topic_works = topic_counts.get(sid, 0)
        reports = int(venues.get(sid, 0)) if venues else 0
        score = 2 * math.log1p(topic_works) + 3 * reports + math.log1p(stats.get("h_index") or 0) / 2
        ranked.append((score, sid, source, topic_works, reports))
    ranked.sort(key=lambda item: item[0], reverse=True)
    for index, (score, sid, source, topic_works, reports) in enumerate(ranked):
        stats = source.get("summary_stats") or {}
        issns = source.get("issn") or ([source["issn_l"]] if source.get("issn_l") else [])
        candidate = existing.get(sid) or models.JournalCandidate(project_id=project.id, openalex_id=sid)
        candidate.name, candidate.issns = source.get("display_name", "")[:500], issns
        candidate.publisher, candidate.homepage = (
            (source.get("host_organization_name") or "")[:500],
            (source.get("homepage_url") or "")[:1000],
        )
        candidate.is_oa, candidate.in_doaj, candidate.apc_usd = (
            source.get("is_oa"),
            source.get("is_in_doaj"),
            source.get("apc_usd"),
        )
        candidate.h_index, candidate.mean_citedness, candidate.works_count = (
            stats.get("h_index"),
            stats.get("2yr_mean_citedness"),
            source.get("works_count"),
        )
        candidate.topic_works, candidate.included_study_reports, candidate.score = topic_works, reports, round(score, 3)
        reasons = []
        if topic_works:
            reasons.append(f"{topic_works} works on this topic in OpenAlex")
        if reports:
            reasons.append(f"Published {reports} of the included study reports")
        warnings = []
        if index < MAX_JOURNAL_CHECKS and issns:
            try:
                doaj = await asyncio.to_thread(doaj_journal, issns[0])
                candidate.in_doaj = (
                    doaj is not None if candidate.in_doaj is None else candidate.in_doaj or doaj is not None
                )
                candidate.medline_indexed = await asyncio.to_thread(medline_indexed, issns[0])
                if doaj and (doaj.get("apc") or {}).get("max"):
                    price = doaj["apc"]["max"][0]
                    reasons.append(f"DOAJ lists a publication fee of {price.get('price')} {price.get('currency')}")
            except SearchError:
                warnings.append("DOAJ or the NLM Catalog couldn't be checked")
        if not issns:
            warnings.append("No ISSN recorded")
        if candidate.is_oa and candidate.in_doaj is False:
            warnings.append("Open access journal not listed in DOAJ")
        if candidate.apc_usd and candidate.in_doaj is False:
            warnings.append("Charges publication fees but isn't in DOAJ")
        if candidate.medline_indexed is False:
            warnings.append("Not currently indexed in MEDLINE")
        if (candidate.works_count or 0) < 100:
            warnings.append("Few published works in OpenAlex")
        candidate.reasons, candidate.warnings = reasons, warnings
        db.add(candidate)
    _audit(db, access, "journals.searched", "project", project.id, {"query": query, "candidates": len(ranked)})
    db.commit()
    return list_journals(access, db)


@router.get("/journals")
def list_journals(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    rows = db.scalars(
        select(models.JournalCandidate)
        .where(models.JournalCandidate.project_id == access.project.id)
        .order_by(models.JournalCandidate.score.desc())
    )
    return {
        "candidates": [candidate_out(c) for c in rows],
        "note": (
            "Warnings are heuristic flags, not verdicts. Check the journal with Think. Check. Submit. "
            "(thinkchecksubmit.org) before submitting."
        ),
    }


class ShortlistIn(BaseModel):
    shortlisted: bool


@router.put("/journals/{candidate_id}")
def shortlist_journal(
    candidate_id: int,
    body: ShortlistIn,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_MANUSCRIPT)),
    db: Session = Depends(get_db),
):
    candidate = get_in_project(db, models.JournalCandidate, candidate_id, access.project.id, "Journal")
    candidate.shortlisted = body.shortlisted
    db.commit()
    return candidate_out(candidate)


# --- Guidelines ---


def _public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=422, detail="Give an http or https address")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, None)}
    except socket.gaierror as exc:
        raise HTTPException(status_code=422, detail="That address couldn't be found") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise HTTPException(status_code=422, detail="Guidelines must be on a public website")


def html_text(content: str) -> str:
    content = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", content)
    content = re.sub(r"(?i)<(br|p|div|li|h[1-6]|tr)[^>]*>", "\n", content)
    return re.sub(r"[ \t]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", content))).strip()


def fetch_guideline(url: str) -> tuple[bytes, str]:
    _public_url(url)
    try:
        response = requests.get(
            url, timeout=30, stream=True, allow_redirects=False, headers={"User-Agent": "OmniReview/1.0"}
        )
        response.raise_for_status()
        content = response.raw.read(MAX_GUIDELINE_BYTES + 1, decode_content=True)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail="The guidelines page couldn't be downloaded") from exc
    if len(content) > MAX_GUIDELINE_BYTES:
        raise HTTPException(status_code=413, detail="The guidelines are larger than 10 MB")
    return content, response.headers.get("content-type", "")


def guideline_out(g: models.JournalGuideline) -> dict:
    return {
        "id": g.id,
        "journal_name": g.journal_name,
        "source_url": g.source_url,
        "file_name": g.file_name,
        "requirements": g.requirements,
        "characters": len(g.content),
        "created_at": g.created_at,
    }


@router.post("/journal-guidelines", status_code=201, dependencies=[Depends(ai_rate_limit)])
async def add_guideline(
    journal_name: str = Form(..., min_length=1, max_length=500),
    url: str = Form("", max_length=1000),
    text: str = Form("", max_length=500_000),
    file: UploadFile | None = File(None),
    access: ProjectAccess = Depends(project_access(Permission.EDIT_MANUSCRIPT)),
    db: Session = Depends(get_db),
):
    """Read a journal's author guidelines (a web page, a PDF or Word file, or pasted text) into requirements, each with
    a quote that must be found in the guidelines."""
    file_name = ""
    if file is not None:
        content = await file.read(MAX_GUIDELINE_BYTES + 1)
        if len(content) > MAX_GUIDELINE_BYTES:
            raise HTTPException(status_code=413, detail="The guidelines are larger than 10 MB")
        file_name = (file.filename or "guidelines")[:255]
        text = _document_text(file_name, content)
    elif url.strip():
        content, content_type = await asyncio.to_thread(fetch_guideline, url.strip())
        text = (
            _document_text("guidelines.pdf", content)
            if "pdf" in content_type
            else html_text(content.decode("utf-8", "replace"))
        )
    if len(text.strip()) < 200:
        raise HTTPException(
            status_code=422, detail="Give the guidelines as a link, a file, or at least a paragraph of text"
        )
    run, output = await _ai_call(db, access, GUIDELINE_PROMPT, lambda ai: read_guideline(ai, journal_name, text))
    guideline = models.JournalGuideline(
        project_id=access.project.id,
        journal_name=journal_name.strip(),
        source_url=url.strip(),
        file_name=file_name,
        content=text,
        requirements=grounded_requirements(output, text),
        ai_run_id=run.id,
        created_by_id=access.user.id,
    )
    db.add(guideline)
    db.flush()
    _audit(
        db,
        access,
        "ai.journal_guideline",
        "journal_guideline",
        guideline.id,
        {"journal": guideline.journal_name, "requirements": list(guideline.requirements)},
    )
    db.commit()
    return guideline_out(guideline)


def _document_text(file_name: str, content: bytes) -> str:
    try:
        parsed = parse_document(content, detect_kind(file_name, content))
    except ParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return "\n".join(span.text for span in parsed.spans)


@router.get("/journal-guidelines")
def list_guidelines(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    rows = db.scalars(
        select(models.JournalGuideline)
        .where(models.JournalGuideline.project_id == access.project.id)
        .order_by(models.JournalGuideline.id.desc())
    )
    return {"guidelines": [guideline_out(g) for g in rows], "requirement_names": REQUIREMENT_NAMES}


class RequirementEdit(BaseModel):
    value: str = Field(max_length=2000)


class GuidelineUpdate(BaseModel):
    requirements: dict[str, RequirementEdit]


@router.put("/journal-guidelines/{guideline_id}")
def update_guideline(
    guideline_id: int,
    body: GuidelineUpdate,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_MANUSCRIPT)),
    db: Session = Depends(get_db),
):
    """Correct requirements by hand; reviewer-entered values are used by readiness checks."""
    guideline = get_in_project(db, models.JournalGuideline, guideline_id, access.project.id, "Guidelines")
    unknown = set(body.requirements) - set(REQUIREMENT_NAMES)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown requirements: {', '.join(sorted(unknown))}")
    requirements = dict(guideline.requirements)
    for name, edit in body.requirements.items():
        if edit.value.strip():
            requirements[name] = {
                **requirements.get(name, {"quotes": []}),
                "value": edit.value.strip(),
                "source": "reviewer",
                "grounded": False,
            }
        else:
            requirements.pop(name, None)
    guideline.requirements = requirements
    _audit(
        db,
        access,
        "journal_guideline.edited",
        "journal_guideline",
        guideline.id,
        {"requirements": {k: v.value for k, v in body.requirements.items()}},
    )
    db.commit()
    return guideline_out(guideline)


# --- Submission packages ---


def package_out(p: models.SubmissionPackage) -> dict:
    return {
        "id": p.id,
        "journal_name": p.journal_name,
        "guideline_id": p.guideline_id,
        "manuscript_version_id": p.manuscript_version_id,
        "cover_letter": p.cover_letter,
        "highlights": p.highlights,
        "files": p.files,
        "sha256": p.sha256,
        "readiness": p.readiness,
        "blocking": len(blocking(p.readiness)),
        "status": p.status,
        "confirmed_at": p.confirmed_at,
        "updated_at": p.updated_at,
    }


def _refresh_readiness(db: Session, access: ProjectAccess, package: models.SubmissionPackage) -> None:
    manuscript = _manuscript(db, access)
    guideline = db.get(models.JournalGuideline, package.guideline_id) if package.guideline_id else None
    package.readiness = readiness(db, access.project, manuscript, guideline, package)


@router.get("/submission-packages")
def list_packages(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    rows = db.scalars(
        select(models.SubmissionPackage)
        .where(models.SubmissionPackage.project_id == access.project.id)
        .order_by(models.SubmissionPackage.id.desc())
    )
    manuscript = get_manuscript(db, access.project.id)
    return {
        "packages": [package_out(p) for p in rows],
        "statements": statements(db, access.project, manuscript) if manuscript else {},
        "statement_labels": STATEMENT_LABELS,
    }


class PackageIn(BaseModel):
    journal_name: str = Field("", max_length=500)
    guideline_id: int | None = None
    cover_letter: str = Field("", max_length=20_000)


@router.post("/submission-packages", status_code=201)
def create_package(
    body: PackageIn,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_MANUSCRIPT)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "submission")
    manuscript = _manuscript(db, access)
    version = latest_version(manuscript)
    if version is None or not approval_state(db, manuscript)["all_approved"]:
        raise HTTPException(status_code=409, detail="Every author must approve the current manuscript version first")
    if body.guideline_id is not None:
        get_in_project(db, models.JournalGuideline, body.guideline_id, access.project.id, "Guidelines")
    package = models.SubmissionPackage(
        project_id=access.project.id,
        manuscript_version_id=version.id,
        guideline_id=body.guideline_id,
        journal_name=body.journal_name.strip(),
        cover_letter=body.cover_letter,
        status="draft",
        created_by_id=access.user.id,
    )
    db.add(package)
    db.flush()
    _refresh_readiness(db, access, package)
    _audit(
        db,
        access,
        "submission_package.created",
        "submission_package",
        package.id,
        {"journal": package.journal_name, "version_id": version.id},
    )
    db.commit()
    return package_out(package)


def _package(db: Session, access: ProjectAccess, package_id: int) -> models.SubmissionPackage:
    return get_in_project(db, models.SubmissionPackage, package_id, access.project.id, "Submission package")


@router.put("/submission-packages/{package_id}")
def update_package(
    package_id: int,
    body: PackageIn,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_MANUSCRIPT)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "submission")
    package = _package(db, access, package_id)
    if body.guideline_id is not None:
        get_in_project(db, models.JournalGuideline, body.guideline_id, access.project.id, "Guidelines")
    package.journal_name, package.guideline_id, package.cover_letter = (
        body.journal_name.strip(),
        body.guideline_id,
        body.cover_letter,
    )
    package.status, package.confirmed_by_id, package.confirmed_at = "draft", None, None
    _refresh_readiness(db, access, package)
    _audit(
        db, access, "submission_package.updated", "submission_package", package.id, {"journal": package.journal_name}
    )
    db.commit()
    return package_out(package)


@router.post("/submission-packages/{package_id}/cover-letter", dependencies=[Depends(ai_rate_limit)])
async def cover_letter(
    package_id: int,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_MANUSCRIPT)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "submission")
    package = _package(db, access, package_id)
    manuscript = _manuscript(db, access)
    rows = summary_of_findings(db, access.project)
    findings = (
        "\n".join(
            f"- {r['outcome']}: {r['informative_statement']} ({r['certainty_label']} certainty; {r['relative_effect']})"
            for r in rows
        )
        or "(no summary of findings)"
    )
    given = statements(db, access.project, manuscript)
    review = (
        f"Title: {manuscript.title}\nQuestion: {access.project.protocol.question if access.project.protocol else ''}"
    )
    statement_text = "\n".join(f"{STATEMENT_LABELS.get(k, k)}: {v}" for k, v in given.items())
    run, text = await _ai_call(
        db,
        access,
        COVER_LETTER_PROMPT,
        lambda ai: draft_cover_letter(ai, package.journal_name or "the journal", review, findings, statement_text),
    )
    package.cover_letter = text.strip()
    package.cover_letter_ai_run_id = run.id
    package.status, package.confirmed_by_id, package.confirmed_at = "draft", None, None
    _refresh_readiness(db, access, package)
    _audit(
        db, access, "ai.cover_letter", "submission_package", package.id, {"provider": run.provider, "model": run.model}
    )
    db.commit()
    return package_out(package)


@router.post("/submission-packages/{package_id}/build")
async def build_package(
    package_id: int,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_MANUSCRIPT)),
    db: Session = Depends(get_db),
):
    """Build the package files from the approved manuscript and the review's records, and check readiness."""
    require_stage_open(db, access.project.id, "submission")
    package = _package(db, access, package_id)
    manuscript = _manuscript(db, access)
    version = latest_version(manuscript)
    if (
        version is None
        or version.id != package.manuscript_version_id
        or not approval_state(db, manuscript)["all_approved"]
    ):
        raise HTTPException(
            status_code=409,
            detail="The manuscript changed or lost approval; start a package from the current approved version",
        )
    files, notes = await build_submission_files(db, access.project, manuscript, package)
    archive = zipped(files)
    storage = document_storage()
    if package.storage_key:
        storage.delete(package.storage_key)
    package.storage_key = storage.save(access.project.id, archive)
    import hashlib

    package.sha256 = hashlib.sha256(archive).hexdigest()
    package.files = manifest(files)
    package.status, package.confirmed_by_id, package.confirmed_at = "built", None, None
    _refresh_readiness(db, access, package)
    _audit(
        db,
        access,
        "submission_package.built",
        "submission_package",
        package.id,
        {
            "sha256": package.sha256,
            "files": len(files),
            "notes": notes,
            "blocking": [c["key"] for c in blocking(package.readiness)],
        },
    )
    db.commit()
    return {**package_out(package), "notes": notes}


@router.get("/submission-packages/{package_id}/download")
def download_package(
    package_id: int, access: ProjectAccess = Depends(project_access(Permission.EXPORT)), db: Session = Depends(get_db)
):
    package = _package(db, access, package_id)
    if not package.storage_key:
        raise HTTPException(status_code=409, detail="Build the package first")
    _audit(db, access, "submission_package.downloaded", "submission_package", package.id, {})
    db.commit()
    return Response(
        document_storage().read(package.storage_key),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="submission-package-{package.id}.zip"'},
    )


@router.post("/submission-packages/{package_id}/confirm")
def confirm_package(
    package_id: int,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    """The corresponding author confirms the package is ready, then submits it on the journal's system themselves."""
    require_stage_open(db, access.project.id, "submission")
    package = _package(db, access, package_id)
    manuscript = _manuscript(db, access)
    corresponding = next((a for a in manuscript.authors if a.corresponding), None)
    if corresponding is None or corresponding.user_id != access.user.id:
        raise HTTPException(status_code=403, detail="Only the corresponding author can confirm the submission package")
    if package.status != "built":
        raise HTTPException(status_code=409, detail="Build the package before confirming it")
    _refresh_readiness(db, access, package)
    failing = blocking(package.readiness)
    if failing:
        db.commit()
        raise HTTPException(status_code=409, detail=f"Readiness checks fail: {'; '.join(c['label'] for c in failing)}")
    package.status, package.confirmed_by_id, package.confirmed_at = "confirmed", access.user.id, models.utcnow()
    _audit(
        db,
        access,
        "submission_package.confirmed",
        "submission_package",
        package.id,
        {"sha256": package.sha256, "journal": package.journal_name},
    )
    db.commit()
    return package_out(package)


# --- Deposits ---


def deposit_out(d: models.RepositoryDeposit) -> dict:
    return {
        "id": d.id,
        "target": d.target,
        "status": d.status,
        "sandbox": d.sandbox,
        "external_id": d.external_id,
        "doi": d.doi,
        "url": d.url,
        "files": d.files,
        "metadata": d.deposit_metadata,
        "error": d.error,
        "release_id": d.release_id,
        "created_at": d.created_at,
    }


class DepositIn(BaseModel):
    target: Literal["zenodo", "figshare", "osf", "github", "gitlab", "dryad", "medrxiv", "osf_preprints"]
    token: str = Field("", max_length=500)
    sandbox: bool = True
    publish: bool = False
    repository: str = Field("", max_length=200, pattern=r"^[A-Za-z0-9_.\-/]*$")
    gitlab_api_url: str = Field("", max_length=300)
    include_manuscript: bool = True
    version: str = Field("1", max_length=40)


async def create_deposit(
    db: Session, access: ProjectAccess, body: DepositIn, release: models.ReviewRelease | None = None
) -> models.RepositoryDeposit:
    project = access.project
    if not stage_completed(db, project.id, "certainty"):
        raise HTTPException(
            status_code=409, detail="Deposit materials once the evidence base is locked (certainty signed off)"
        )
    if body.target not in PACKAGED_TARGETS and len(body.token) < 10:
        raise HTTPException(
            status_code=422, detail="Give an access token for the repository (used for this deposit only)"
        )
    if body.target in ("github", "gitlab") and not body.repository:
        raise HTTPException(status_code=422, detail="Name the repository")
    manuscript = get_manuscript(db, project.id)
    files = await fair_package(db, project, body.version, body.include_manuscript)
    title = f"{manuscript.title if manuscript else project.title}: data, code, and materials"
    description = (
        "Search strategies, the locked extraction data set, analysis scripts and results, and reporting checklists."
    )
    creators = [
        {
            "name": a.name,
            **({"affiliation": a.affiliation} if a.affiliation else {}),
            **({"orcid": a.orcid} if a.orcid else {}),
        }
        for a in (manuscript.authors if manuscript else [])
    ]
    metadata = zenodo_metadata(title, description, creators, manuscript.keywords if manuscript else [], body.version)
    deposit = models.RepositoryDeposit(
        project_id=project.id,
        target=body.target,
        status="failed",
        sandbox=body.sandbox,
        files=manifest(files),
        deposit_metadata={"title": title, "version": body.version},
        release_id=release.id if release else None,
        created_by_id=access.user.id,
    )
    db.add(deposit)
    try:
        if body.target in PACKAGED_TARGETS:
            deposit.storage_key = document_storage().save(
                project.id, zipped({**files, "SUBMISSION_STEPS.md": PACKAGED_TARGETS[body.target].encode()})
            )
            deposit.status = "packaged"
            deposit.deposit_metadata = {**deposit.deposit_metadata, "instructions": PACKAGED_TARGETS[body.target]}
        elif body.target == "zenodo":
            previous = ""
            if release is not None:
                prior = db.scalar(
                    select(models.RepositoryDeposit)
                    .where(
                        models.RepositoryDeposit.project_id == project.id,
                        models.RepositoryDeposit.target == "zenodo",
                        models.RepositoryDeposit.status == "published",
                        models.RepositoryDeposit.sandbox == body.sandbox,
                        models.RepositoryDeposit.release_id.is_not(None),
                    )
                    .order_by(models.RepositoryDeposit.id.desc())
                    .limit(1)
                )
                previous = prior.external_id if prior else ""
            result = await asyncio.to_thread(
                zenodo_deposit, body.token, body.sandbox, metadata, files, body.publish, previous
            )
            deposit.status, deposit.external_id, deposit.url, deposit.doi, deposit.concept_id = (
                result.status,
                result.external_id,
                result.url,
                result.doi,
                result.concept_id,
            )
        elif body.target == "figshare":
            result = await asyncio.to_thread(figshare_deposit, body.token, metadata, files, body.publish)
            deposit.status, deposit.external_id, deposit.url, deposit.doi = (
                result.status,
                result.external_id,
                result.url,
                result.doi,
            )
        elif body.target == "osf":
            result = await asyncio.to_thread(osf_deposit, body.token, title, description, files)
            deposit.status, deposit.external_id, deposit.url = result.status, result.external_id, result.url
        elif body.target == "github":
            result = await asyncio.to_thread(github_deposit, body.token, body.repository, title, files)
            deposit.status, deposit.external_id, deposit.url = result.status, result.external_id, result.url
        else:
            result = await asyncio.to_thread(
                gitlab_deposit, body.token, body.repository, title, files, body.gitlab_api_url
            )
            deposit.status, deposit.external_id, deposit.url = result.status, result.external_id, result.url
    except RepositoryError as exc:
        deposit.status, deposit.error = "failed", str(exc)
    db.flush()
    _audit(
        db,
        access,
        "deposit.created",
        "repository_deposit",
        deposit.id,
        {
            "target": body.target,
            "status": deposit.status,
            "doi": deposit.doi,
            "sandbox": body.sandbox,
            "published": body.publish,
            "files": len(files),
        },
    )
    db.commit()
    return deposit


@router.get("/deposits")
def list_deposits(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    rows = db.scalars(
        select(models.RepositoryDeposit)
        .where(models.RepositoryDeposit.project_id == access.project.id)
        .order_by(models.RepositoryDeposit.id.desc())
    )
    return [deposit_out(d) for d in rows]


@router.post("/deposits", status_code=201)
async def deposit(
    body: DepositIn, access: ProjectAccess = Depends(project_access(Permission.EXPORT)), db: Session = Depends(get_db)
):
    """Deposit the review's data, code, and materials (FAIR package with README, data dictionary, licence, and DataCite
    metadata). Tokens are used for this request only. Zenodo and Figshare deposits stay drafts unless publish is set."""
    result = await create_deposit(db, access, body)
    if result.status == "failed":
        raise HTTPException(status_code=502, detail=result.error or "The deposit failed")
    return deposit_out(result)


@router.get("/deposits/{deposit_id}/package")
def deposit_package(
    deposit_id: int, access: ProjectAccess = Depends(project_access(Permission.EXPORT)), db: Session = Depends(get_db)
):
    row = get_in_project(db, models.RepositoryDeposit, deposit_id, access.project.id, "Deposit")
    if not row.storage_key:
        raise HTTPException(status_code=404, detail="This deposit has no downloadable package")
    return Response(
        document_storage().read(row.storage_key),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{row.target}-package-{row.id}.zip"'},
    )


# --- Peer review ---


def comment_out(c: models.ReviewerComment) -> dict:
    return {
        "id": c.id,
        "reviewer": c.reviewer,
        "number": c.number,
        "body": c.body,
        "category": c.category,
        "response": c.response,
        "status": c.status,
        "changes": c.changes,
        "reanalysis": c.reanalysis,
        "updated_at": c.updated_at,
    }


def round_out(r: models.ReviewRound) -> dict:
    return {
        "id": r.id,
        "journal": r.journal,
        "round_number": r.round_number,
        "decision": r.decision,
        "received_on": r.received_on,
        "manuscript_version_id": r.manuscript_version_id,
        "response_letter": r.response_letter,
        "status": r.status,
        "comments": [comment_out(c) for c in r.comments],
        "open_comments": sum(1 for c in r.comments if c.status == "open"),
    }


def _round(db: Session, access: ProjectAccess, round_id: int) -> models.ReviewRound:
    return get_in_project(db, models.ReviewRound, round_id, access.project.id, "Review round")


@router.get("/peer-review/rounds")
def list_rounds(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    rows = db.scalars(
        select(models.ReviewRound)
        .where(models.ReviewRound.project_id == access.project.id)
        .order_by(models.ReviewRound.round_number)
    )
    return [round_out(r) for r in rows]


class RoundIn(BaseModel):
    journal: str = Field("", max_length=500)
    decision: str = Field("", max_length=40)
    received_on: str = Field("", max_length=20)


@router.post("/peer-review/rounds", status_code=201)
def create_round(
    body: RoundIn,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_MANUSCRIPT)),
    db: Session = Depends(get_db),
):
    manuscript = _manuscript(db, access)
    version = latest_version(manuscript)
    count = len(list_rounds(access, db))
    row = models.ReviewRound(
        project_id=access.project.id,
        journal=body.journal,
        decision=body.decision,
        received_on=body.received_on,
        round_number=count + 1,
        manuscript_version_id=version.id if version else None,
        status="open",
        created_by_id=access.user.id,
    )
    db.add(row)
    db.flush()
    _audit(
        db,
        access,
        "peer_review.round_created",
        "review_round",
        row.id,
        {"journal": row.journal, "decision": row.decision},
    )
    db.commit()
    db.refresh(row)
    return round_out(row)


class RoundUpdate(RoundIn):
    response_letter: str = Field("", max_length=100_000)
    status: Literal["open", "responded"] = "open"


@router.put("/peer-review/rounds/{round_id}")
def update_round(
    round_id: int,
    body: RoundUpdate,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_MANUSCRIPT)),
    db: Session = Depends(get_db),
):
    row = _round(db, access, round_id)
    if body.status == "responded" and any(c.status == "open" for c in row.comments):
        raise HTTPException(status_code=409, detail="Respond to every comment first")
    row.journal, row.decision, row.received_on, row.response_letter, row.status = (
        body.journal,
        body.decision,
        body.received_on,
        body.response_letter,
        body.status,
    )
    _audit(
        db,
        access,
        "peer_review.round_updated",
        "review_round",
        row.id,
        {"status": row.status, "decision": row.decision},
    )
    db.commit()
    return round_out(row)


class ImportComments(BaseModel):
    text: str = Field(min_length=10, max_length=300_000)
    use_ai: bool = False


async def _import_comments(
    db: Session, access: ProjectAccess, row: models.ReviewRound, text: str, use_ai: bool
) -> dict:
    dropped = 0
    if use_ai:
        run, output = await _ai_call(db, access, REVIEW_COMMENTS_PROMPT, lambda ai: split_comments(ai, text))
        comments, dropped = grounded_comments(output, text)
    else:
        comments = rule_split_comments(text)
    for comment in comments:
        row.comments.append(
            models.ReviewerComment(
                reviewer=comment["reviewer"],
                number=comment["number"],
                body=comment["body"],
                category=comment["category"],
                status="open",
                updated_by_id=access.user.id,
            )
        )
    _audit(
        db,
        access,
        "peer_review.comments_imported",
        "review_round",
        row.id,
        {"comments": len(comments), "dropped_unverbatim": dropped, "ai": use_ai},
    )
    db.commit()
    db.refresh(row)
    return {**round_out(row), "imported": len(comments), "dropped": dropped}


@router.post("/peer-review/rounds/{round_id}/comments/import")
async def import_comments(
    round_id: int,
    body: ImportComments,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_MANUSCRIPT)),
    db: Session = Depends(get_db),
):
    """Split a pasted review report into comments by reviewer headings and numbering, or with AI (verbatim only)."""
    return await _import_comments(db, access, _round(db, access, round_id), body.text, body.use_ai)


@router.post("/peer-review/rounds/{round_id}/comments/upload")
async def upload_comments(
    round_id: int,
    file: UploadFile = File(...),
    use_ai: bool = Form(False),
    access: ProjectAccess = Depends(project_access(Permission.EDIT_MANUSCRIPT)),
    db: Session = Depends(get_db),
):
    content = await file.read(MAX_GUIDELINE_BYTES + 1)
    if len(content) > MAX_GUIDELINE_BYTES:
        raise HTTPException(status_code=413, detail="The file is larger than 10 MB")
    return await _import_comments(
        db, access, _round(db, access, round_id), _document_text(file.filename or "report.txt", content), use_ai
    )


class CommentIn(BaseModel):
    reviewer: str = Field(min_length=1, max_length=100)
    number: str = Field("", max_length=20)
    body: str = Field(min_length=1, max_length=50_000)
    category: Literal["", "major", "minor", "editorial", "methods", "statistics", "other"] = ""


@router.post("/peer-review/rounds/{round_id}/comments", status_code=201)
def add_comment(
    round_id: int,
    body: CommentIn,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_MANUSCRIPT)),
    db: Session = Depends(get_db),
):
    row = _round(db, access, round_id)
    row.comments.append(
        models.ReviewerComment(
            reviewer=body.reviewer,
            number=body.number,
            body=body.body,
            category=body.category,
            status="open",
            updated_by_id=access.user.id,
        )
    )
    db.commit()
    db.refresh(row)
    return round_out(row)


class ChangeIn(BaseModel):
    section_key: str = Field(max_length=40)
    revision_id: int


class CommentUpdate(BaseModel):
    response: str = Field("", max_length=50_000)
    status: Literal["open", "addressed", "rebutted"] = "open"
    category: Literal["", "major", "minor", "editorial", "methods", "statistics", "other"] = ""
    changes: list[ChangeIn] = Field(default_factory=list, max_length=50)


def _comment(db: Session, access: ProjectAccess, comment_id: int) -> models.ReviewerComment:
    comment = db.get(models.ReviewerComment, comment_id)
    if comment is None or db.get(models.ReviewRound, comment.round_id).project_id != access.project.id:  # type: ignore[union-attr]
        raise HTTPException(status_code=404, detail="Comment not found")
    return comment


@router.put("/peer-review/comments/{comment_id}")
def update_comment(
    comment_id: int,
    body: CommentUpdate,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_MANUSCRIPT)),
    db: Session = Depends(get_db),
):
    comment = _comment(db, access, comment_id)
    if body.status != "open" and not body.response.strip():
        raise HTTPException(status_code=422, detail="Write a response before marking the comment addressed or rebutted")
    manuscript = _manuscript(db, access)
    sections = {s.key: s.id for s in manuscript.sections}
    for change in body.changes:
        revision = db.get(models.ManuscriptRevision, change.revision_id)
        if (
            change.section_key not in sections
            or revision is None
            or revision.section_id != sections[change.section_key]
        ):
            raise HTTPException(
                status_code=422, detail=f"Revision {change.revision_id} isn't a revision of {change.section_key}"
            )
    comment.response, comment.status, comment.category = body.response, body.status, body.category
    comment.changes = [c.model_dump() for c in body.changes]
    comment.updated_by_id = access.user.id
    _audit(
        db,
        access,
        "peer_review.comment_updated",
        "reviewer_comment",
        comment.id,
        {"status": comment.status, "changes": comment.changes},
    )
    db.commit()
    return comment_out(comment)


@router.delete("/peer-review/comments/{comment_id}", status_code=204)
def delete_comment(
    comment_id: int,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_MANUSCRIPT)),
    db: Session = Depends(get_db),
):
    comment = _comment(db, access, comment_id)
    db.delete(comment)
    db.commit()
    return Response(status_code=204)


class ReanalysisIn(BaseModel):
    stage: str = Field(max_length=30)
    rationale: str = Field(min_length=10, max_length=2000)


@router.post("/peer-review/comments/{comment_id}/reanalysis")
def request_reanalysis(
    comment_id: int,
    body: ReanalysisIn,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    """Reopen a workflow stage (and every later one) in response to a reviewer comment, so the change goes back through
    the same gates."""
    comment = _comment(db, access, comment_id)
    if body.stage not in STAGES:
        raise HTTPException(status_code=422, detail="Unknown workflow stage")
    if not access.can(STAGE_PERMISSIONS[body.stage]):
        raise HTTPException(status_code=403, detail=f"Your role can't reopen {STAGE_LABELS[body.stage]}")
    rationale = f"Peer review ({comment.reviewer} comment {comment.number}): {body.rationale}"
    try:
        reopened = reopen_stage(db, access.project, body.stage, access.user, rationale)
    except WorkflowError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    comment.reanalysis = {
        "stage": body.stage,
        "rationale": body.rationale,
        "reopened": reopened,
        "reopened_at": models.utcnow().isoformat(),
    }
    db.commit()
    return comment_out(comment)


@router.get("/peer-review/rounds/{round_id}/changes")
def round_changes(
    round_id: int,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    """What changed in the manuscript since the version that was reviewed."""
    from manuscript_routes import _word_diff

    row = _round(db, access, round_id)
    manuscript = _manuscript(db, access)
    reviewed = db.get(models.ManuscriptVersion, row.manuscript_version_id) if row.manuscript_version_id else None
    before = reviewed.content["sections"] if reviewed else {}
    current = version_content(db, manuscript)["sections"]
    return [
        {
            "key": key,
            "title": current.get(key, before.get(key, {})).get("title", key),
            "diff": _word_diff(before.get(key, {}).get("content", ""), current.get(key, {}).get("content", "")),
        }
        for key in dict.fromkeys([*before, *current])
        if before.get(key, {}).get("content") != current.get(key, {}).get("content")
    ]


@router.post("/peer-review/rounds/{round_id}/response-letter", dependencies=[Depends(ai_rate_limit)])
async def response_letter(
    round_id: int,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_MANUSCRIPT)),
    db: Session = Depends(get_db),
):
    row = _round(db, access, round_id)
    if not row.comments:
        raise HTTPException(status_code=409, detail="Import the reviewers' comments first")
    manuscript = _manuscript(db, access)
    titles = {s.key: s.title for s in manuscript.sections}
    text = "\n\n".join(
        f"{c.reviewer}, comment {c.number}: {c.body}\nResponse: {c.response or '(none yet)'}\nChanges: "
        f"{', '.join(titles.get(ch['section_key'], ch['section_key']) for ch in c.changes) or 'none recorded'}"
        + (f"\nRe-analysis: {c.reanalysis['stage']} reopened ({c.reanalysis['rationale']})" if c.reanalysis else "")
        for c in row.comments
    )
    run, letter = await _ai_call(
        db, access, RESPONSE_LETTER_PROMPT, lambda ai: draft_response_letter(ai, row.journal or "the journal", text)
    )
    row.response_letter, row.response_ai_run_id = letter.strip(), run.id
    _audit(db, access, "ai.response_letter", "review_round", row.id, {"provider": run.provider, "model": run.model})
    db.commit()
    return round_out(row)


@router.get("/peer-review/rounds/{round_id}/response-letter.docx")
def response_letter_docx(
    round_id: int,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    row = _round(db, access, round_id)
    document = styled_document()
    document.add_heading(f"Response to reviewers (round {row.round_number})", level=1)
    for paragraph in (row.response_letter or "").split("\n\n"):
        document.add_paragraph(paragraph)
    if not row.response_letter:
        for c in row.comments:
            document.add_heading(f"{c.reviewer}, comment {c.number}", level=2)
            document.add_paragraph(c.body)
            document.add_paragraph(f"Response: {c.response}")
    output = io.BytesIO()
    document.save(output)
    return Response(
        output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="response-round-{row.round_number}.docx"'},
    )
