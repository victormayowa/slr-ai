"""Registries: OSF Registries (review protocols) and ClinicalTrials.gov (registered studies).

PROSPERO has no public search API, so reviewers search it themselves; see topic_exploration.PROSPERO_SEARCH_URL.
"""

import logging

import requests

from services.errors import SearchError

logger = logging.getLogger(__name__)

OSF_REGISTRATIONS_URL = "https://api.osf.io/v2/registrations/"
CLINICALTRIALS_STUDIES_URL = "https://clinicaltrials.gov/api/v2/studies"
REQUEST_TIMEOUT_SECONDS = 30


def search_osf_registrations(query: str, limit: int = 10) -> list[dict]:
    """Public OSF registrations whose titles contain the query."""
    try:
        response = requests.get(
            OSF_REGISTRATIONS_URL,
            params={"filter[title]": query, "page[size]": str(limit)},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        items = response.json().get("data", [])
    except (requests.RequestException, ValueError) as exc:
        logger.warning("OSF Registries search failed", exc_info=True)
        raise SearchError("OSF Registries search failed. Please try again shortly.") from exc

    registrations = []
    for item in items:
        attributes = item.get("attributes") or {}
        registrations.append(
            {
                "id": item.get("id") or "",
                "title": attributes.get("title") or "No Title",
                "registered": (attributes.get("date_registered") or "")[:10],
                "url": (item.get("links") or {}).get("html") or f"https://osf.io/{item.get('id', '')}",
            }
        )
    return registrations


def count_clinical_trials(query: str) -> int:
    """How many studies registered on ClinicalTrials.gov match the query."""
    try:
        response = requests.get(
            CLINICALTRIALS_STUDIES_URL,
            params={"query.term": query, "countTotal": "true", "pageSize": "1", "fields": "NCTId"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return int(response.json().get("totalCount") or 0)
    except (requests.RequestException, ValueError) as exc:
        logger.warning("ClinicalTrials.gov search failed", exc_info=True)
        raise SearchError("ClinicalTrials.gov search failed. Please try again shortly.") from exc
