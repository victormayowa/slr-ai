import logging
import os

import requests

from services.errors import SearchError

logger = logging.getLogger(__name__)

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
REQUEST_TIMEOUT_SECONDS = 30


def _abstract_from_inverted_index(inverted_index: dict | None) -> str:
    """OpenAlex stores abstracts as {word: [positions]}; rebuild the original word order."""
    if not inverted_index:
        return ""
    positioned = [(position, word) for word, positions in inverted_index.items() for position in positions]
    return " ".join(word for _, word in sorted(positioned))


def _get_works(params: dict[str, str | int]) -> dict:
    """Query the works endpoint, identifying the caller as OpenAlex asks and using an API key when configured."""
    params = dict(params)
    if email := os.getenv("OPENALEX_EMAIL"):
        params["mailto"] = email
    if api_key := os.getenv("OPENALEX_API_KEY"):
        params["api_key"] = api_key
    try:
        response = requests.get(OPENALEX_WORKS_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("OpenAlex search failed", exc_info=True)
        raise SearchError("OpenAlex search failed. Please try again shortly.") from exc


def _authors(work: dict) -> str:
    names = [(a.get("author") or {}).get("display_name") or "" for a in work.get("authorships", [])]
    names = [name for name in names if name]
    return ", ".join(names[:3]) + (" et al." if len(names) > 3 else "")


def _work_record(work: dict) -> dict:
    source = (work.get("primary_location") or {}).get("source") or {}
    ids = work.get("ids") or {}
    openalex_id = (work.get("id") or "").split("/")[-1]
    return {
        "id": openalex_id,
        "title": work.get("title") or "No Title",
        "authors": _authors(work),
        "year": work.get("publication_year") or "",
        "source": "OpenAlex",
        "venue": source.get("display_name") or "",
        "doi": (work.get("doi") or "").replace("https://doi.org/", ""),
        "abstract": _abstract_from_inverted_index(work.get("abstract_inverted_index")),
        "identifiers": {
            key: value
            for key, value in {
                "openalex": openalex_id,
                "pmid": (ids.get("pmid") or "").rstrip("/").split("/")[-1],
                "pmcid": (ids.get("pmcid") or "").rstrip("/").split("/")[-1],
            }.items()
            if value
        },
        "url": work.get("doi") or work.get("id") or "",
    }


def openalex_search(query: str, limit: int) -> tuple[list[dict], int]:
    """Works matching the query, up to `limit`, and the total OpenAlex reports."""
    records: list[dict] = []
    cursor: str | None = "*"
    total = 0
    while len(records) < limit and cursor:
        data = _get_works({"search": query, "per-page": min(200, limit - len(records)), "cursor": cursor})
        meta = data.get("meta") or {}
        total = int(meta.get("count") or 0)
        results = data.get("results", [])
        records += [_work_record(work) for work in results]
        cursor = meta.get("next_cursor")
        if not results or not cursor:
            break
    return records[:limit], total


def search_openalex(query: str, max_results: int = 50) -> list[dict]:
    return openalex_search(query, max_results)[0]


def openalex_counts_by_year(query: str) -> tuple[int, dict[int, int]]:
    """The number of works matching the query, in total and by publication year."""
    data = _get_works({"search": query, "group_by": "publication_year"})
    by_year = {
        int(group["key"]): int(group.get("count") or 0)
        for group in data.get("group_by", [])
        if str(group.get("key", "")).isdigit()
    }
    total = int((data.get("meta") or {}).get("count") or sum(by_year.values()))
    return total, by_year


def find_openalex_reviews(query: str, limit: int = 5) -> list[dict]:
    """The most recent works OpenAlex classifies as reviews (narrative or systematic) that match the query."""
    data = _get_works({"search": query, "filter": "type:review", "sort": "publication_date:desc", "per-page": limit})
    reviews = []
    for work in data.get("results", []):
        source = (work.get("primary_location") or {}).get("source") or {}
        doi = (work.get("doi") or "").replace("https://doi.org/", "")
        reviews.append(
            {
                "id": (work.get("id") or "").split("/")[-1],
                "source": "OpenAlex",
                "title": work.get("title") or "No Title",
                "year": str(work.get("publication_year") or ""),
                "venue": source.get("display_name") or "",
                "doi": doi,
                "url": work.get("doi") or work.get("id") or "",
                "authors": _authors(work),
            }
        )
    return reviews


def openalex_found_seeds(query: str, pmids: list[str], dois: list[str]) -> set[str]:
    """Which of these PMIDs and DOIs the query retrieves in OpenAlex."""
    found: set[str] = set()
    if dois:
        data = _get_works({"search": query, "filter": "doi:" + "|".join(dois), "per-page": 200})
        retrieved = {(w.get("doi") or "").replace("https://doi.org/", "").lower() for w in data.get("results", [])}
        found |= retrieved & set(dois)
    if pmids:
        data = _get_works({"search": query, "filter": "pmid:" + "|".join(pmids), "per-page": 200})
        retrieved = {
            ((w.get("ids") or {}).get("pmid") or "").rstrip("/").split("/")[-1] for w in data.get("results", [])
        }
        found |= retrieved & set(pmids)
    return found
