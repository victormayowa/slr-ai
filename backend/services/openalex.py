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


def search_openalex(query: str, max_results: int = 50) -> list[dict]:
    params: dict[str, str | int] = {"search": query, "per-page": min(max_results, 200)}
    if email := os.getenv("OPENALEX_EMAIL"):
        params["mailto"] = email
    if api_key := os.getenv("OPENALEX_API_KEY"):
        params["api_key"] = api_key

    try:
        response = requests.get(OPENALEX_WORKS_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("OpenAlex search failed", exc_info=True)
        raise SearchError("OpenAlex search failed. Please try again shortly.") from exc

    results = []
    for work in data.get("results", []):
        authors = [(a.get("author") or {}).get("display_name") or "" for a in work.get("authorships", [])]
        authors = [name for name in authors if name]
        source = (work.get("primary_location") or {}).get("source") or {}
        results.append(
            {
                "id": (work.get("id") or "").split("/")[-1],
                "title": work.get("title") or "No Title",
                "authors": ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else ""),
                "year": work.get("publication_year") or "",
                "source": "OpenAlex",
                "venue": source.get("display_name") or "",
                "doi": (work.get("doi") or "").replace("https://doi.org/", ""),
                "abstract": _abstract_from_inverted_index(work.get("abstract_inverted_index")),
            }
        )
    return results
