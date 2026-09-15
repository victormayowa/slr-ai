"""MeSH (Medical Subject Headings) lookup through the NCBI E-utilities."""

import logging

import requests

from services.errors import SearchError
from services.pubmed import EUTILS_BASE, REQUEST_TIMEOUT_SECONDS, _eutils_params

logger = logging.getLogger(__name__)


def lookup_mesh(term: str, limit: int = 8) -> list[dict]:
    """Headings matching the term, each with its entry terms (synonyms), scope note, and tree numbers."""
    try:
        search = requests.get(
            f"{EUTILS_BASE}/esearch.fcgi",
            params=_eutils_params(db="mesh", term=term, retmode="json", retmax=limit),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        search.raise_for_status()
        ids = search.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []
        summary = requests.get(
            f"{EUTILS_BASE}/esummary.fcgi",
            params=_eutils_params(db="mesh", id=",".join(ids), retmode="json", version="2.0"),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        summary.raise_for_status()
        result = summary.json().get("result", {})
    except (requests.RequestException, ValueError) as exc:
        logger.warning("MeSH lookup failed", exc_info=True)
        raise SearchError("The MeSH lookup failed. Please try again shortly.") from exc

    headings = []
    for uid in ids:
        item = result.get(uid) or {}
        terms = [term for term in item.get("ds_meshterms") or [] if term]
        if not terms:
            continue
        headings.append(
            {
                "ui": item.get("ds_meshui") or "",
                "heading": terms[0],
                "entry_terms": terms[1:25],
                "scope_note": (item.get("ds_scopenote") or "").strip(),
                "tree_numbers": [link.get("treenum") for link in item.get("ds_idxlinks") or [] if link.get("treenum")],
            }
        )
    return headings
