"""Checking references against their sources: DOI metadata from Crossref, PubMed records, retraction notices (Crossref,
which includes the Retraction Watch database, and PubMed publication types), and Zotero libraries.

Zotero keys are used for the request only and never stored or logged.
"""

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import requests
from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

CROSSREF_WORKS_URL = "https://api.crossref.org/works"
EUTILS_SUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
ZOTERO_API_URL = "https://api.zotero.org"
TIMEOUT_SECONDS = 30
RETRACTION_TYPES = {"retraction", "withdrawal", "removal"}


class ReferenceCheckError(Exception):
    """A lookup failed. The message is safe to show users."""


@dataclass
class CheckResult:
    status: str
    details: dict[str, Any] = field(default_factory=dict)
    retracted: bool = False
    retraction: dict[str, Any] = field(default_factory=dict)
    source: dict[str, Any] | None = None


def _headers() -> dict[str, str]:
    email = os.getenv("CROSSREF_MAILTO") or os.getenv("UNPAYWALL_EMAIL") or ""
    return {"User-Agent": f"OmniReview/1.0 (mailto:{email})" if email else "OmniReview/1.0"}


def _get(url: str, params: dict[str, str] | None = None) -> requests.Response:
    try:
        return requests.get(url, params=params, headers=_headers(), timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        logger.warning("Reference lookup failed: %s", type(exc).__name__)
        raise ReferenceCheckError("The reference lookup failed. Try again shortly.") from exc


def crossref_work(doi: str) -> dict[str, Any] | None:
    response = _get(f"{CROSSREF_WORKS_URL}/{quote(doi, safe='/')}")
    if response.status_code == 404:
        return None
    if not response.ok:
        raise ReferenceCheckError(f"Crossref returned an error ({response.status_code})")
    return response.json().get("message")


def crossref_retractions(doi: str, work: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Retraction, withdrawal, or removal notices for the DOI."""
    notices = [
        {
            "type": u.get("type"),
            "doi": u.get("DOI", ""),
            "date": (u.get("updated") or {}).get("date-time", ""),
            "source": u.get("source", "crossref"),
        }
        for u in (work or {}).get("updated-by", [])
        if u.get("type") in RETRACTION_TYPES
    ]
    response = _get(CROSSREF_WORKS_URL, {"filter": f"updates:{doi}", "rows": "20"})
    if response.ok:
        for item in response.json().get("message", {}).get("items", []):
            for update in item.get("update-to", []):
                if update.get("DOI", "").lower() == doi.lower() and update.get("type") in RETRACTION_TYPES:
                    notices.append(
                        {
                            "type": update.get("type"),
                            "doi": item.get("DOI", ""),
                            "date": (update.get("updated") or {}).get("date-time", ""),
                            "source": update.get("source", "crossref"),
                        }
                    )
    unique = {(n["doi"], n["type"]): n for n in notices}
    return list(unique.values())


def pubmed_summary(pmid: str) -> dict[str, Any] | None:
    params = {"db": "pubmed", "id": pmid, "retmode": "json"}
    if key := os.getenv("NCBI_API_KEY"):
        params["api_key"] = key
    response = _get(EUTILS_SUMMARY_URL, params)
    if not response.ok:
        raise ReferenceCheckError(f"PubMed returned an error ({response.status_code})")
    result = response.json().get("result", {})
    summary = result.get(pmid)
    return summary if summary and not summary.get("error") else None


def _normalized(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", re.sub(r"<[^>]+>", "", title or "").lower()).strip()


def _compare(csl: dict[str, Any], title: str, year: str) -> dict[str, Any]:
    similarity = fuzz.token_sort_ratio(_normalized(csl.get("title", "")), _normalized(title))
    ours = ((csl.get("issued") or {}).get("date-parts") or [[None]])[0][0]
    year_matches = not ours or not year or abs(int(ours) - int(year)) <= 1
    return {
        "title_similarity": round(similarity, 1),
        "source_title": title,
        "source_year": year,
        "year_matches": year_matches,
    }


def check_reference(csl: dict[str, Any], doi: str, pmid: str) -> CheckResult:
    """Verify metadata and look for retractions. Raises ReferenceCheckError when a service can't be reached."""
    if doi:
        work = crossref_work(doi)
        if work is None:
            return CheckResult("not_found", {"message": "Crossref has no record of this DOI"})
        issued = (work.get("issued") or {}).get("date-parts") or [[None]]
        comparison = _compare(csl, (work.get("title") or [""])[0], str(issued[0][0] or ""))
        notices = crossref_retractions(doi, work)
        status = "verified" if comparison["title_similarity"] >= 90 and comparison["year_matches"] else "mismatch"
        return CheckResult(status, comparison, bool(notices), {"notices": notices} if notices else {}, work)
    if pmid:
        summary = pubmed_summary(pmid)
        if summary is None:
            return CheckResult("not_found", {"message": "PubMed has no record of this PMID"})
        comparison = _compare(csl, summary.get("title", ""), (summary.get("pubdate") or "")[:4])
        retracted = "Retracted Publication" in summary.get("pubtype", [])
        status = "verified" if comparison["title_similarity"] >= 90 and comparison["year_matches"] else "mismatch"
        retraction = (
            {"notices": [{"type": "retraction", "source": "pubmed", "doi": "", "date": ""}]} if retracted else {}
        )
        return CheckResult(status, comparison, retracted, retraction, summary)
    return CheckResult("not_found", {"message": "Add a DOI or PMID so the reference can be verified"})


def _zotero_base(library_type: str, library_id: str) -> str:
    if library_type not in ("user", "group") or not library_id.isdigit():
        raise ReferenceCheckError("Give a Zotero user or group library id (a number)")
    return f"{ZOTERO_API_URL}/{library_type}s/{library_id}"


def zotero_items(api_key: str, library_type: str, library_id: str, collection_key: str = "") -> list[dict[str, Any]]:
    """Items from a Zotero library or collection, as CSL-JSON."""
    base = _zotero_base(library_type, library_id)
    url = f"{base}/collections/{collection_key}/items/top" if collection_key else f"{base}/items/top"
    items: list[dict[str, Any]] = []
    start = 0
    while True:
        try:
            response = requests.get(
                url,
                params={"format": "csljson", "limit": "100", "start": str(start)},
                headers={"Zotero-API-Key": api_key, "Zotero-API-Version": "3"},
                timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise ReferenceCheckError("Zotero couldn't be reached. Try again shortly.") from exc
        if response.status_code in (401, 403):
            raise ReferenceCheckError("Zotero rejected the API key or it can't read this library")
        if not response.ok:
            raise ReferenceCheckError(f"Zotero returned an error ({response.status_code})")
        batch = response.json().get("items", [])
        items += batch
        if len(batch) < 100 or len(items) >= 2000:
            return items
        start += 100


def zotero_create(
    api_key: str, library_type: str, library_id: str, items: list[dict[str, Any]], collection_key: str = ""
) -> int:
    """Create items in a Zotero library (in batches of 50). Returns how many were created."""
    base = _zotero_base(library_type, library_id)
    created = 0
    for start in range(0, len(items), 50):
        batch = [
            {**item, "collections": [collection_key] if collection_key else []} for item in items[start : start + 50]
        ]
        try:
            response = requests.post(
                f"{base}/items",
                json=batch,
                headers={"Zotero-API-Key": api_key, "Zotero-API-Version": "3"},
                timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise ReferenceCheckError("Zotero couldn't be reached. Try again shortly.") from exc
        if response.status_code in (401, 403):
            raise ReferenceCheckError("Zotero rejected the API key or it can't write to this library")
        if not response.ok:
            raise ReferenceCheckError(f"Zotero returned an error ({response.status_code})")
        created += len(response.json().get("successful", {}))
    return created
