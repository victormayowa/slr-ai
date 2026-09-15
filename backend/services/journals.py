"""Journal information: OpenAlex sources (topic match, open access, fees, metrics), DOAJ listing, and MEDLINE indexing
from the NLM Catalog. Warnings built from these are heuristics, not verdicts on a journal."""

import logging
import os
from collections import Counter
from typing import Any

import requests

from services.errors import SearchError

logger = logging.getLogger(__name__)

OPENALEX_URL = "https://api.openalex.org"
DOAJ_URL = "https://doaj.org/api/search/journals"
NLM_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
TIMEOUT_SECONDS = 30
SOURCE_FIELDS = (
    "id,display_name,issn_l,issn,host_organization_name,is_oa,is_in_doaj,apc_usd,summary_stats,works_count,"
    "homepage_url,type"
)


def _get(url: str, params: dict[str, str]) -> dict[str, Any]:
    if url.startswith(OPENALEX_URL):
        if email := os.getenv("OPENALEX_EMAIL"):
            params["mailto"] = email
        if key := os.getenv("OPENALEX_API_KEY"):
            params["api_key"] = key
    try:
        response = requests.get(url, params=params, timeout=TIMEOUT_SECONDS)
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Journal lookup failed: %s", type(exc).__name__)
        raise SearchError("The journal lookup failed. Try again shortly.") from exc


def _tail(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


def topic_sources(query: str, limit: int = 25) -> list[tuple[str, int]]:
    """Sources publishing the most works matching the query, with the number of matching works."""
    data = _get(f"{OPENALEX_URL}/works", {"search": query, "group_by": "primary_location.source.id"})
    groups = [g for g in data.get("group_by", []) if g.get("key") and "unknown" not in str(g.get("key"))]
    return [(_tail(g["key"]), int(g.get("count") or 0)) for g in groups[:limit]]


def sources_for_dois(dois: list[str]) -> Counter[str]:
    """How many of these works each source published."""
    counts: Counter[str] = Counter()
    for start in range(0, len(dois), 50):
        chunk = [d.lower() for d in dois[start : start + 50]]
        data = _get(
            f"{OPENALEX_URL}/works",
            {"filter": "doi:" + "|".join(chunk), "per-page": "50", "select": "primary_location"},
        )
        for work in data.get("results", []):
            source = ((work.get("primary_location") or {}).get("source") or {}).get("id")
            if source:
                counts[_tail(source)] += 1
    return counts


def sources_by_ids(source_ids: list[str]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for start in range(0, len(source_ids), 50):
        chunk = source_ids[start : start + 50]
        data = _get(
            f"{OPENALEX_URL}/sources",
            {"filter": "openalex:" + "|".join(chunk), "per-page": "50", "select": SOURCE_FIELDS},
        )
        sources += data.get("results", [])
    return sources


def doaj_journal(issn: str) -> dict[str, Any] | None:
    data = _get(f"{DOAJ_URL}/issn:{issn}", {})
    results = data.get("results") or []
    return results[0].get("bibjson", {}) if results else None


def medline_indexed(issn: str) -> bool:
    params = {"db": "nlmcatalog", "term": f"{issn}[issn] AND currentlyindexed[All]", "retmode": "json"}
    if key := os.getenv("NCBI_API_KEY"):
        params["api_key"] = key
    data = _get(NLM_ESEARCH_URL, params)
    return int((data.get("esearchresult") or {}).get("count") or 0) > 0
