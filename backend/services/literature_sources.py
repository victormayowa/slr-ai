"""Search connectors for Europe PMC, Crossref, ClinicalTrials.gov, and Semantic Scholar.

Each returns (records, total available) and pages through results up to the requested limit. PubMed and OpenAlex live
in their own modules.
"""

import logging
import os
import re
from typing import Any

import requests

from services.errors import SearchError

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 30
EUROPEPMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
CROSSREF_URL = "https://api.crossref.org/works"
CLINICALTRIALS_URL = "https://clinicaltrials.gov/api/v2/studies"
SEMANTIC_SCHOLAR_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
# Semantic Scholar's search endpoint stops at 1,000 results.
SEMANTIC_SCHOLAR_MAX = 1000


def _strip_tags(text: str | None) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", text or "").split())


def _get_json(label: str, url: str, params: dict[str, str], headers: dict[str, str] | None = None) -> Any:
    try:
        response = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        logger.warning("%s search failed", label, exc_info=True)
        raise SearchError(f"{label} search failed. Please try again shortly.") from exc
    if response.status_code == 429:
        hint = " Add SEMANTIC_SCHOLAR_API_KEY on the server for higher limits." if label == "Semantic Scholar" else ""
        raise SearchError(f"{label} is rate limiting requests. Try again shortly.{hint}")
    try:
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("%s search failed", label, exc_info=True)
        raise SearchError(f"{label} search failed. Please try again shortly.") from exc


def search_europepmc(query: str, limit: int) -> tuple[list[dict], int]:
    records: list[dict] = []
    cursor, total = "*", 0
    while len(records) < limit:
        data = _get_json(
            "Europe PMC",
            EUROPEPMC_URL,
            {
                "query": query,
                "format": "json",
                "resultType": "core",
                "pageSize": str(min(1000, limit - len(records))),
                "cursorMark": cursor,
            },
        )
        total = int(data.get("hitCount") or 0)
        results = (data.get("resultList") or {}).get("result", [])
        for item in results:
            journal = ((item.get("journalInfo") or {}).get("journal") or {}).get("title") or ""
            records.append(
                {
                    "id": item.get("id") or "",
                    "title": _strip_tags(item.get("title")) or "No Title",
                    "authors": item.get("authorString") or "",
                    "year": item.get("pubYear") or "",
                    "venue": journal,
                    "doi": item.get("doi") or "",
                    "abstract": _strip_tags(item.get("abstractText")),
                    "identifiers": {
                        key: value
                        for key, value in {
                            "pmid": item.get("pmid"),
                            "pmcid": item.get("pmcid"),
                            "europepmc": f"{item.get('source')}:{item.get('id')}",
                        }.items()
                        if value
                    },
                    "url": f"https://europepmc.org/article/{item.get('source')}/{item.get('id')}",
                }
            )
        next_cursor = data.get("nextCursorMark")
        if not results or not next_cursor or next_cursor == cursor or len(records) >= total:
            break
        cursor = next_cursor
    return records[:limit], total


def _crossref_authors(authors: list[dict]) -> str:
    names = [" ".join(part for part in (a.get("family"), a.get("given")) if part) or a.get("name", "") for a in authors]
    return "; ".join(name for name in names if name)


def search_crossref(query: str, limit: int) -> tuple[list[dict], int]:
    records: list[dict] = []
    cursor, total = "*", 0
    email = os.getenv("CROSSREF_EMAIL") or os.getenv("OPENALEX_EMAIL")
    while len(records) < limit:
        params = {
            "query": query,
            "rows": str(min(1000, limit - len(records))),
            "cursor": cursor,
            "select": "DOI,title,author,issued,container-title,abstract,URL",
        }
        if email:
            params["mailto"] = email
        message = _get_json("Crossref", CROSSREF_URL, params).get("message") or {}
        total = int(message.get("total-results") or 0)
        items = message.get("items", [])
        for item in items:
            year_parts = ((item.get("issued") or {}).get("date-parts") or [[None]])[0]
            records.append(
                {
                    "id": item.get("DOI") or "",
                    "title": _strip_tags((item.get("title") or [""])[0]) or "No Title",
                    "authors": _crossref_authors(item.get("author") or []),
                    "year": str(year_parts[0] or "") if year_parts else "",
                    "venue": (item.get("container-title") or [""])[0],
                    "doi": item.get("DOI") or "",
                    "abstract": _strip_tags(item.get("abstract")),
                    "identifiers": {},
                    "url": item.get("URL") or "",
                }
            )
        next_cursor = message.get("next-cursor")
        if not items or not next_cursor:
            break
        cursor = next_cursor
    return records[:limit], total


def search_clinical_trials(query: str, limit: int) -> tuple[list[dict], int]:
    records: list[dict] = []
    total, page_token = 0, ""
    while len(records) < limit:
        params = {"query.term": query, "pageSize": str(min(1000, limit - len(records))), "countTotal": "true"}
        if page_token:
            params["pageToken"] = page_token
        data = _get_json("ClinicalTrials.gov", CLINICALTRIALS_URL, params)
        total = int(data.get("totalCount") or total)
        studies = data.get("studies", [])
        for study in studies:
            protocol = study.get("protocolSection") or {}
            identification = protocol.get("identificationModule") or {}
            nct = identification.get("nctId") or ""
            start = ((protocol.get("statusModule") or {}).get("startDateStruct") or {}).get("date") or ""
            sponsor = ((protocol.get("sponsorCollaboratorsModule") or {}).get("leadSponsor") or {}).get("name") or ""
            records.append(
                {
                    "id": nct,
                    "title": identification.get("officialTitle") or identification.get("briefTitle") or "No Title",
                    "authors": sponsor,
                    "year": start[:4],
                    "venue": "ClinicalTrials.gov",
                    "doi": "",
                    "abstract": (protocol.get("descriptionModule") or {}).get("briefSummary") or "",
                    "identifiers": {"nct": nct} if nct else {},
                    "url": f"https://clinicaltrials.gov/study/{nct}" if nct else "",
                }
            )
        page_token = data.get("nextPageToken") or ""
        if not studies or not page_token:
            break
    return records[:limit], total


def search_semantic_scholar(query: str, limit: int) -> tuple[list[dict], int]:
    limit = min(limit, SEMANTIC_SCHOLAR_MAX)
    headers = {"x-api-key": key} if (key := os.getenv("SEMANTIC_SCHOLAR_API_KEY")) else None
    records: list[dict] = []
    total = 0
    while len(records) < limit:
        batch = min(100, limit - len(records))
        data = _get_json(
            "Semantic Scholar",
            SEMANTIC_SCHOLAR_URL,
            {
                "query": query,
                "offset": str(len(records)),
                "limit": str(batch),
                "fields": "title,abstract,year,authors,externalIds,venue,url",
            },
            headers,
        )
        total = int(data.get("total") or 0)
        papers = data.get("data", [])
        for paper in papers:
            external = paper.get("externalIds") or {}
            pmcid = external.get("PubMedCentral")
            records.append(
                {
                    "id": paper.get("paperId") or "",
                    "title": paper.get("title") or "No Title",
                    "authors": "; ".join(a.get("name", "") for a in paper.get("authors") or [] if a.get("name")),
                    "year": str(paper.get("year") or ""),
                    "venue": paper.get("venue") or "",
                    "doi": external.get("DOI") or "",
                    "abstract": paper.get("abstract") or "",
                    "identifiers": {
                        key: value
                        for key, value in {
                            "s2": paper.get("paperId"),
                            "pmid": external.get("PubMed"),
                            "pmcid": f"PMC{pmcid}" if pmcid else None,
                        }.items()
                        if value
                    },
                    "url": paper.get("url") or "",
                }
            )
        if len(papers) < batch or len(records) >= total:
            break
    return records[:limit], total
