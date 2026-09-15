"""Citation searching through OpenAlex: backward (the references of chosen records) and forward (the works citing them).

Studies found this way that the database searches missed are reported under "other methods" in PRISMA 2020.
"""

from dataclasses import dataclass, field
from typing import Literal

import models
from services.openalex import openalex_citing, openalex_raw_works, openalex_records
from services.record_import import normalize_doi

Direction = Literal["backward", "forward", "both"]
# Caps the references fetched in one run, so a run over many records stays a reasonable size.
MAX_REFERENCES = 5000


@dataclass
class Candidate:
    record: dict
    seed_record_ids: set[int] = field(default_factory=set)
    directions: set[str] = field(default_factory=set)


@dataclass
class ChaseResult:
    candidates: list[Candidate]
    # Chosen records OpenAlex couldn't identify from their OpenAlex ID, DOI, or PMID.
    unresolved_seed_ids: list[int]


def _tail(url: str | None) -> str:
    return (url or "").rstrip("/").split("/")[-1]


def resolve_openalex_ids(seeds: list[models.Record]) -> dict[int, str]:
    """OpenAlex work IDs of the records, from their stored OpenAlex ID, else their DOI, else their PMID."""
    resolved = {s.id: s.identifiers["openalex"] for s in seeds if (s.identifiers or {}).get("openalex")}
    by_doi = {normalize_doi(s.doi).lower(): s.id for s in seeds if s.id not in resolved and s.doi}
    if by_doi:
        for work in openalex_raw_works("doi", list(by_doi), "id,doi"):
            doi = normalize_doi(work.get("doi") or "").lower()
            if doi in by_doi:
                resolved[by_doi[doi]] = _tail(work.get("id"))
    by_pmid = {
        str(s.identifiers["pmid"]): s.id for s in seeds if s.id not in resolved and (s.identifiers or {}).get("pmid")
    }
    if by_pmid:
        for work in openalex_raw_works("pmid", list(by_pmid), "id,ids"):
            pmid = _tail((work.get("ids") or {}).get("pmid"))
            if pmid in by_pmid:
                resolved[by_pmid[pmid]] = _tail(work.get("id"))
    return resolved


def chase(seeds: list[models.Record], direction: Direction, limit_per_seed: int) -> ChaseResult:
    ids = resolve_openalex_ids(seeds)
    seed_by_work = {work_id: seed_id for seed_id, work_id in ids.items()}
    candidates: dict[str, Candidate] = {}

    if direction in ("backward", "both") and seed_by_work:
        references: dict[str, set[int]] = {}
        for work in openalex_raw_works("openalex", list(seed_by_work), "id,referenced_works"):
            seed_id = seed_by_work.get(_tail(work.get("id")))
            if seed_id is None:
                continue
            for reference in work.get("referenced_works") or []:
                references.setdefault(_tail(reference), set()).add(seed_id)
        for record in openalex_records(list(references)[:MAX_REFERENCES]):
            candidate = candidates.setdefault(record["id"], Candidate(record))
            candidate.seed_record_ids |= references.get(record["id"], set())
            candidate.directions.add("backward")

    if direction in ("forward", "both"):
        for work_id, seed_id in seed_by_work.items():
            citing, _ = openalex_citing(work_id, limit_per_seed)
            for record in citing:
                candidate = candidates.setdefault(record["id"], Candidate(record))
                candidate.seed_record_ids.add(seed_id)
                candidate.directions.add("forward")

    # A chosen record isn't a new find, even when chosen records cite each other.
    for work_id in seed_by_work:
        candidates.pop(work_id, None)
    return ChaseResult(list(candidates.values()), [seed.id for seed in seeds if seed.id not in ids])
