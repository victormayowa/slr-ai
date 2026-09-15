"""Duplicate detection. Records sharing an identifier, or with the same title and year, are duplicates for certain and
are marked automatically. Records with similar titles are only candidates, for a reviewer to decide."""

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from services.record_import import normalize_doi

CANDIDATE_TITLE_SIMILARITY = 90
MAX_CANDIDATES = 200
_IDENTIFIERS = ("pmid", "pmcid", "nct")


@dataclass
class CandidatePair:
    record_id: int
    other_id: int
    score: float
    reasons: list[str]


def normalized_title(title: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def _doi(record: models.Record) -> str:
    return normalize_doi(record.doi or "").lower()


def _year(record: models.Record) -> int | None:
    match = re.search(r"\d{4}", record.year or "")
    return int(match.group(0)) if match else None


def _identifier_keys(record: models.Record) -> set[str]:
    keys = {f"doi:{doi}"} if (doi := _doi(record)) else set()
    identifiers = record.identifiers or {}
    for name in _IDENTIFIERS:
        value = str(identifiers.get(name) or "").strip().lower()
        if value:
            keys.add(f"{name}:{value}")
    return keys


def find_duplicates(records: Iterable[models.Record]) -> dict[int, int]:
    """Map each duplicate not yet marked to the earliest record it duplicates.

    A duplicate shares a DOI, PMID, PMCID, or trial registration number with an earlier record, or has the same
    normalized title and year when the two records' DOIs don't conflict.
    """
    kept_by_key: dict[str, int] = {}
    kept_by_title: dict[str, list[models.Record]] = defaultdict(list)
    duplicates: dict[int, int] = {}
    for record in sorted(records, key=lambda r: r.id):
        if record.duplicate_of_id is not None:
            continue
        keys = _identifier_keys(record)
        title_key = f"{normalized_title(record.title)}|{(record.year or '').strip()}"
        match = next((kept_by_key[key] for key in keys if key in kept_by_key), None)
        if match is None and normalized_title(record.title):
            own_doi = _doi(record)
            match = next(
                (
                    kept.id
                    for kept in kept_by_title[title_key]
                    if not own_doi or not _doi(kept) or _doi(kept) == own_doi
                ),
                None,
            )
        if match is not None:
            duplicates[record.id] = match
            for key in keys:
                kept_by_key.setdefault(key, match)
            continue
        for key in keys:
            kept_by_key[key] = record.id
        kept_by_title[title_key].append(record)
    return duplicates


def reviewed_pairs(db: Session, project_id: int) -> set[tuple[int, int]]:
    rows = db.execute(
        select(models.DuplicateReview.record_id, models.DuplicateReview.other_record_id).where(
            models.DuplicateReview.project_id == project_id
        )
    )
    return {(row.record_id, row.other_record_id) for row in rows}


def _first_author_surname(record: models.Record) -> str:
    first = re.split(r"[;,]| and ", record.authors or "", maxsplit=1)[0]
    words = normalized_title(first).split()
    return max(words, key=len) if words else ""


def candidate_pairs(records: Iterable[models.Record], reviewed: set[tuple[int, int]]) -> list[CandidatePair]:
    """Pairs of unique records with very similar titles and close years that a reviewer hasn't decided on yet."""
    all_records = list(records)
    automatic = find_duplicates(all_records)
    unique = [
        r for r in all_records if r.duplicate_of_id is None and r.id not in automatic and normalized_title(r.title)
    ]
    by_year: dict[int | None, list[models.Record]] = defaultdict(list)
    for record in unique:
        by_year[_year(record)].append(record)

    pairs: dict[tuple[int, int], CandidatePair] = {}

    def compare(group: list[models.Record], others: list[models.Record]) -> None:
        choices = [normalized_title(other.title) for other in others]
        for record in group:
            matches = process.extract(
                normalized_title(record.title),
                choices,
                scorer=fuzz.token_sort_ratio,
                score_cutoff=CANDIDATE_TITLE_SIMILARITY,
                limit=None,
            )
            for _, score, index in matches:
                other = others[index]
                key = (min(record.id, other.id), max(record.id, other.id))
                if other.id == record.id or key in reviewed or key in pairs:
                    continue
                reasons = [f"Titles {score:.0f}% similar"]
                year, other_year = _year(record), _year(other)
                if year is not None and year == other_year:
                    reasons.append("Same year")
                elif year is not None and other_year is not None:
                    reasons.append("Years differ by one")
                if _first_author_surname(record) and _first_author_surname(record) == _first_author_surname(other):
                    reasons.append("Same first author")
                if _doi(record) and _doi(other) and _doi(record) != _doi(other):
                    reasons.append("Different DOIs")
                pairs[key] = CandidatePair(key[0], key[1], round(score, 1), reasons)

    for year, group in by_year.items():
        if year is None:
            compare(group, unique)
            continue
        compare(group, group)
        compare(group, by_year.get(year + 1, []))
    return sorted(pairs.values(), key=lambda pair: (-pair.score, pair.record_id, pair.other_id))[:MAX_CANDIDATES]
