"""Studies and their reports. Several records can describe one study (for example a protocol, the main results, and a
long-term follow-up). Every report included at full text belongs to exactly one study; each starts as its own study,
and reviewers link reports that describe the same study, helped by shared trial registration numbers and by similar
titles and authors. Extraction and PRISMA's "studies included" count are per study.
"""

import re
from dataclasses import dataclass
from itertools import combinations

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

import models

REGISTRY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ClinicalTrials.gov", re.compile(r"\bNCT\d{8}\b", re.IGNORECASE)),
    ("ISRCTN", re.compile(r"\bISRCTN\d{8}\b", re.IGNORECASE)),
    ("EU CTR (CTIS)", re.compile(r"\b20\d{2}-5\d{5}-\d{2}-\d{2}\b")),
    ("EudraCT", re.compile(r"\b(?:19|20)\d{2}-\d{6}-\d{2}\b")),
    ("ANZCTR", re.compile(r"\bACTRN\d{14}\b", re.IGNORECASE)),
    ("ChiCTR", re.compile(r"\bChiCTR[-A-Za-z]*\d{6,}\b")),
    ("DRKS", re.compile(r"\bDRKS\d{8}\b", re.IGNORECASE)),
    ("CTRI", re.compile(r"\bCTRI/\d{4}/\d{2,3}/\d{6}\b", re.IGNORECASE)),
    ("jRCT", re.compile(r"\bjRCTs?\d{10}\b")),
    ("UMIN-CTR", re.compile(r"\bUMIN\d{9}\b", re.IGNORECASE)),
    ("PACTR", re.compile(r"\bPACTR\d{15}\b", re.IGNORECASE)),
    ("IRCT", re.compile(r"\bIRCT\d{8,14}N\d{1,3}\b", re.IGNORECASE)),
    ("CRiS", re.compile(r"\bKCT\d{7}\b", re.IGNORECASE)),
    ("Netherlands Trial Register", re.compile(r"\bNTR\d{3,5}\b", re.IGNORECASE)),
    ("TCTR", re.compile(r"\bTCTR\d{11}\b", re.IGNORECASE)),
    ("ReBEC", re.compile(r"\bRBR-[0-9a-z]{6,8}\b", re.IGNORECASE)),
    ("SLCTR", re.compile(r"\bSLCTR/\d{4}/\d{3}\b", re.IGNORECASE)),
]
_CASE_SENSITIVE = ("ChiCTR", "jRCT")


def registry_ids(text: str) -> list[str]:
    """Trial registration numbers in the text, normalized and de-duplicated, in order of appearance."""
    found: list[tuple[int, str]] = []
    for name, pattern in REGISTRY_PATTERNS:
        for match in pattern.finditer(text or ""):
            value = match.group(0) if name in _CASE_SENSITIVE else match.group(0).upper()
            if name == "EudraCT" and re.fullmatch(r"20\d{2}-5\d{5}-\d{2}", value):
                continue
            found.append((match.start(), value))
    return list(dict.fromkeys(value for _, value in sorted(found)))


def record_registry_ids(record: models.Record, extra_text: str = "") -> list[str]:
    identifiers = " ".join(str(value) for value in (record.identifiers or {}).values())
    return registry_ids(" ".join([identifiers, record.title, record.abstract, extra_text]))


def document_text_by_record(db: Session, record_ids: list[int]) -> dict[int, str]:
    """The text of each record's parsed documents, joined, for finding registration numbers."""
    rows = db.execute(
        select(models.Document.record_id, models.DocumentSpan.text)
        .join(models.DocumentSpan, models.DocumentSpan.document_id == models.Document.id)
        .where(models.Document.record_id.in_(record_ids), models.DocumentSpan.kind != "reference")
    )
    texts: dict[int, list[str]] = {}
    for record_id, text in rows:
        texts.setdefault(record_id, []).append(text)
    return {record_id: " ".join(parts) for record_id, parts in texts.items()}


def _first_author_surname(authors: str) -> str:
    first = re.split(r";|,(?=\s*[A-Z][a-z])| and ", authors or "", maxsplit=1)[0].strip()
    first = first.split(",")[0].strip()
    words = [word for word in re.split(r"\s+", first) if word]
    if not words:
        return ""
    # "Smith J" and "Smith, John" put the surname first; "John Smith" puts it last.
    return words[0] if len(words) == 1 or len(words[-1]) <= 2 or words[-1].isupper() else words[-1]


def study_label(record: models.Record) -> str:
    surname = _first_author_surname(record.authors)
    if surname and record.year:
        return f"{surname} {record.year}"[:300]
    return (record.title or f"Record {record.id}")[:300]


def _surnames(authors: str) -> set[str]:
    names = set()
    for part in re.split(r";|, (?=[A-Z][a-z]+ [A-Z])| and ", authors or ""):
        surname = _first_author_surname(part)
        if len(surname) > 1:
            names.add(surname.casefold())
    return names


def ensure_studies(db: Session, project_id: int, records: list[models.Record], actor_id: int | None) -> None:
    """Give every record included at full text a study of its own, if it isn't in one yet."""
    if not records:
        return
    linked = set(
        db.scalars(
            select(models.StudyReport.record_id).where(models.StudyReport.record_id.in_([r.id for r in records]))
        )
    )
    texts = document_text_by_record(db, [r.id for r in records if r.id not in linked])
    for record in records:
        if record.id in linked:
            continue
        study = models.Study(
            project_id=project_id,
            label=study_label(record),
            registry_ids=record_registry_ids(record, texts.get(record.id, "")),
            created_by_id=actor_id,
        )
        study.reports.append(models.StudyReport(record_id=record.id, is_primary=True, linked_by_id=actor_id))
        db.add(study)
    db.flush()


def project_studies(db: Session, project_id: int) -> list[models.Study]:
    return list(
        db.scalars(
            select(models.Study)
            .where(models.Study.project_id == project_id)
            .options(
                selectinload(models.Study.reports).selectinload(models.StudyReport.record),
                selectinload(models.Study.arms),
            )
            .order_by(models.Study.id)
        )
    )


@dataclass
class LinkCandidate:
    record_id: int
    other_record_id: int
    score: float
    reasons: list[str]


def link_candidates(db: Session, project_id: int, studies: list[models.Study]) -> list[LinkCandidate]:
    """Pairs of reports in different studies that may describe the same study, most likely first."""
    reports = [(study, report.record) for study in studies for report in study.reports]
    rejected = {
        tuple(sorted((row.record_id, row.other_record_id)))
        for row in db.scalars(select(models.StudyLinkDecision).where(models.StudyLinkDecision.project_id == project_id))
    }
    texts = document_text_by_record(db, [record.id for _, record in reports])
    ids = {record.id: set(record_registry_ids(record, texts.get(record.id, ""))) for _, record in reports}
    candidates = []
    for (study_a, a), (study_b, b) in combinations(reports, 2):
        if study_a.id == study_b.id or tuple(sorted((a.id, b.id))) in rejected:
            continue
        reasons: list[str] = []
        shared = sorted(ids[a.id] & ids[b.id])
        if shared:
            reasons.append(f"Same trial registration: {', '.join(shared)}")
            score = 1.0
        else:
            title = fuzz.token_set_ratio(a.title.casefold(), b.title.casefold()) / 100
            authors_a, authors_b = _surnames(a.authors), _surnames(b.authors)
            overlap = len(authors_a & authors_b) / max(1, min(len(authors_a), len(authors_b)))
            if title < 0.6 or overlap < 0.5:
                continue
            score = round(0.5 * title + 0.4 * overlap, 3)
            reasons.append(f"Similar titles ({title:.0%})")
            reasons.append(f"Shared authors ({overlap:.0%} of the shorter author list)")
        a_first, b_first = (a, b) if a.id < b.id else (b, a)
        candidates.append(LinkCandidate(a_first.id, b_first.id, score, reasons))
    return sorted(candidates, key=lambda c: (-c.score, c.record_id, c.other_record_id))


def study_has_extraction(db: Session, study_id: int) -> bool:
    for model in (models.ExtractionValue, models.ExtractionFinal):
        if db.scalar(select(model.id).where(model.study_id == study_id).limit(1)) is not None:
            return True
    return False
