"""Topic exploration: how much is published on a topic, which reviews and registrations already exist, and whether a
review looks feasible.

Every figure names its source, a source that fails is reported rather than skipped silently, and estimates state the
assumptions behind them.
"""

import asyncio
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any

from services.errors import SearchError
from services.openalex import find_openalex_reviews, openalex_counts_by_year
from services.pubmed import count_pubmed, find_pubmed_reviews
from services.registries import count_clinical_trials, search_osf_registrations

logger = logging.getLogger(__name__)

PROSPERO_SEARCH_URL = "https://www.crd.york.ac.uk/prospero/"
# A review at least this many years old, with randomized trials published since, may be out of date.
OUTDATED_AFTER_YEARS = 5
MAX_OUTDATED_CHECKS = 5
TREND_YEARS = 20
# NCBI allows three requests a second without an API key.
PUBMED_PAUSE_SECONDS = 0.0 if os.getenv("NCBI_API_KEY") else 0.35


@dataclass
class WorkloadAssumptions:
    reviewers: int = 2
    minutes_per_abstract: float = 0.5
    # Share of records that reach full-text review, and share of those that are included.
    full_text_fraction: float = 0.05
    minutes_per_full_text: float = 5
    include_fraction: float = 0.3
    hours_per_included_study: float = 1.5


def _attempt[T](label: str, call: Callable[[], T]) -> tuple[T | None, str | None]:
    try:
        return call(), None
    except SearchError as exc:
        return None, str(exc)
    except Exception:
        logger.exception("%s lookup failed", label)
        return None, f"{label} lookup failed unexpectedly."


def _pause() -> None:
    if PUBMED_PAUSE_SECONDS:
        time.sleep(PUBMED_PAUSE_SECONDS)


def randomized_trials_term(query: str, since_year: int | None = None) -> str:
    term = f"({query}) AND randomized controlled trial[pt]"
    if since_year is not None:
        term += f' AND ("{since_year}"[dp] : "3000"[dp])'
    return term


def _pubmed_lookups(query: str) -> dict[str, Any]:
    total = _attempt("PubMed", lambda: count_pubmed(query))
    _pause()
    trials = _attempt("PubMed", lambda: count_pubmed(randomized_trials_term(query)))
    _pause()
    reviews, reviews_error = _attempt("PubMed", lambda: find_pubmed_reviews(query))
    reviews = reviews or []
    this_year = datetime.now(UTC).year
    checks = 0
    for review in reviews:
        review["newer_randomized_trials"] = None
        review["possibly_outdated"] = False
        year = int(review["year"]) if str(review.get("year", "")).isdigit() else None
        if year is None or year > this_year - OUTDATED_AFTER_YEARS or checks >= MAX_OUTDATED_CHECKS:
            continue
        checks += 1
        _pause()
        newer, _ = _attempt("PubMed", partial(count_pubmed, randomized_trials_term(query, year + 1)))
        review["newer_randomized_trials"] = newer
        review["possibly_outdated"] = bool(newer)
    return {"total": total, "trials": trials, "reviews": (reviews, reviews_error)}


def _openalex_lookups(query: str) -> dict[str, Any]:
    counts = _attempt("OpenAlex", lambda: openalex_counts_by_year(query))
    reviews, error = _attempt("OpenAlex", lambda: find_openalex_reviews(query))
    for review in reviews or []:
        review["newer_randomized_trials"] = None
        review["possibly_outdated"] = False
    return {"counts": counts, "reviews": (reviews or [], error)}


def _registry_lookups(query: str) -> dict[str, Any]:
    return {
        "trials": _attempt("ClinicalTrials.gov", lambda: count_clinical_trials(query)),
        "registrations": _attempt("OSF Registries", lambda: search_osf_registrations(query)),
    }


def _title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def merge_reviews(*groups: list[dict]) -> list[dict]:
    """Combine review lists, keeping the first copy of a review found by DOI or title in more than one source."""
    seen: set[str] = set()
    merged = []
    for group in groups:
        for review in group:
            keys = {key for key in (review.get("doi", "").lower(), _title_key(review.get("title", ""))) if key}
            if keys & seen:
                continue
            seen |= keys
            merged.append({**review, "ref": f"{review['source']}:{review['id']}"})
    return merged


def meta_analysis_feasibility(randomized_trials: int | None) -> dict[str, Any]:
    if randomized_trials is None:
        level, text = "unknown", "PubMed couldn't be searched for randomized trials."
    elif randomized_trials >= 5:
        level = "likely"
        text = (
            f"{randomized_trials} randomized trials match these terms in PubMed, so pooling may be possible if their "
            "outcomes are comparable."
        )
    elif randomized_trials >= 2:
        level = "possible"
        text = (
            f"Only {randomized_trials} randomized trials match these terms in PubMed. A meta-analysis may be possible "
            "but fragile; plan a synthesis without meta-analysis (SWiM) as a fallback."
        )
    else:
        level = "unlikely"
        text = (
            f"{randomized_trials} randomized trial{'' if randomized_trials == 1 else 's'} match these terms in "
            "PubMed. A meta-analysis is unlikely; plan a synthesis without meta-analysis (SWiM) or widen the question."
        )
    note = " The count includes trials of any comparison and matters only for intervention reviews."
    return {"level": level, "randomized_trials": randomized_trials, "explanation": text + note}


def estimate_workload(counts: list[int | None], assumptions: WorkloadAssumptions) -> dict[str, Any] | None:
    available = [count for count in counts if count is not None]
    if not available:
        return None

    def hours_for(records: int) -> dict[str, float]:
        screening = records * assumptions.minutes_per_abstract * assumptions.reviewers / 60
        full_texts = records * assumptions.full_text_fraction
        full_text_hours = full_texts * assumptions.minutes_per_full_text * assumptions.reviewers / 60
        included = full_texts * assumptions.include_fraction
        extraction = included * assumptions.hours_per_included_study * assumptions.reviewers
        return {
            "records": records,
            "full_texts": round(full_texts),
            "included_studies": round(included),
            "screening_hours": round(screening, 1),
            "full_text_hours": round(full_text_hours, 1),
            "extraction_hours": round(extraction, 1),
            "total_hours": round(screening + full_text_hours + extraction, 1),
        }

    return {
        "low": hours_for(max(available)),
        "high": hours_for(sum(available)),
        "explanation": (
            "A planning estimate, not a prediction. The low figure assumes the databases find the same records and the "
            "high figure assumes no overlap. Database counts also include records outside the review's scope."
        ),
    }


async def explore_topic(query: str, assumptions: WorkloadAssumptions) -> dict[str, Any]:
    pubmed, openalex, registries = await asyncio.gather(
        asyncio.to_thread(_pubmed_lookups, query),
        asyncio.to_thread(_openalex_lookups, query),
        asyncio.to_thread(_registry_lookups, query),
    )
    openalex_counts, openalex_error = openalex["counts"]
    openalex_total, by_year = openalex_counts if openalex_counts else (None, {})
    this_year = datetime.now(UTC).year
    trend = (
        {str(year): by_year.get(year, 0) for year in range(this_year - TREND_YEARS + 1, this_year + 1)}
        if by_year
        else {}
    )
    pubmed_reviews, pubmed_reviews_error = pubmed["reviews"]
    openalex_reviews, openalex_reviews_error = openalex["reviews"]
    registrations, registrations_error = registries["registrations"]

    def source(label: str, lookup: tuple[int | None, str | None]) -> dict[str, Any]:
        return {"label": label, "count": lookup[0], "error": lookup[1]}

    return {
        "query": query,
        "run_at": datetime.now(UTC).isoformat(),
        "sources": {
            "pubmed": source("PubMed records", pubmed["total"]),
            "pubmed_randomized_trials": source("PubMed randomized trials", pubmed["trials"]),
            "openalex": source("OpenAlex works", (openalex_total, openalex_error)),
            "clinicaltrials_gov": source("ClinicalTrials.gov registered studies", registries["trials"]),
        },
        "publications_by_year": trend,
        "existing_reviews": merge_reviews(pubmed_reviews, openalex_reviews),
        "review_errors": [error for error in (pubmed_reviews_error, openalex_reviews_error) if error],
        "registrations": registrations or [],
        "registration_error": registrations_error,
        "prospero_search_url": PROSPERO_SEARCH_URL,
        "meta_analysis_feasibility": meta_analysis_feasibility(pubmed["trials"][0]),
        "workload": estimate_workload([pubmed["total"][0], openalex_total], assumptions),
        "notes": [
            "Counts come from each source's own search of your terms, so they include records outside the "
            "review's scope.",
            "Reviews flagged as possibly outdated are at least five years old, with randomized trials in PubMed "
            "published since; check whether those trials are relevant.",
            "PROSPERO has no public search API. Search it yourself before registering.",
        ],
    }
