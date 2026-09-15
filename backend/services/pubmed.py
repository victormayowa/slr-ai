import logging
import os
import xml.etree.ElementTree as ET

import requests

from services.errors import SearchError

logger = logging.getLogger(__name__)

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
REQUEST_TIMEOUT_SECONDS = 30


def _eutils_params(**params) -> dict:
    """Identify the tool to NCBI, as their usage policy asks, and use an API key when configured."""
    params = {"db": "pubmed", "tool": "omnireview", **params}
    if os.getenv("NCBI_EMAIL"):
        params["email"] = os.getenv("NCBI_EMAIL")
    if os.getenv("NCBI_API_KEY"):
        params["api_key"] = os.getenv("NCBI_API_KEY")
    return params


def _text(element) -> str:
    return "".join(element.itertext()).strip() if element is not None else ""


def _parse_article(article) -> dict | None:
    pmid = article.findtext("MedlineCitation/PMID", default="")
    details = article.find("MedlineCitation/Article")
    if not pmid or details is None:
        return None

    abstract_parts = []
    for section in details.findall("Abstract/AbstractText"):
        text = _text(section)
        if text:
            label = section.get("Label")
            abstract_parts.append(f"{label}: {text}" if label else text)

    authors = []
    for author in details.findall("AuthorList/Author"):
        name = " ".join(filter(None, [author.findtext("LastName"), author.findtext("Initials")]))
        name = name or author.findtext("CollectiveName") or ""
        if name:
            authors.append(name)

    year = ""
    pub_date = details.find("Journal/JournalIssue/PubDate")
    if pub_date is not None:
        year = pub_date.findtext("Year") or (pub_date.findtext("MedlineDate") or "")[:4]

    doi = next(
        (
            article_id.text.strip()
            for article_id in article.findall("PubmedData/ArticleIdList/ArticleId")
            if article_id.get("IdType") == "doi" and article_id.text
        ),
        "",
    )

    pmcid = next(
        (
            article_id.text.strip()
            for article_id in article.findall("PubmedData/ArticleIdList/ArticleId")
            if article_id.get("IdType") == "pmc" and article_id.text
        ),
        "",
    )

    return {
        "id": pmid,
        "title": _text(details.find("ArticleTitle")) or "No Title",
        "authors": ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else ""),
        "year": year,
        "source": "PubMed",
        "venue": details.findtext("Journal/Title", default=""),
        "doi": doi,
        "abstract": "\n".join(abstract_parts),
        "identifiers": {"pmid": pmid, **({"pmcid": pmcid} if pmcid else {})},
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    }


def pubmed_search(query: str, limit: int) -> tuple[list[dict], int]:
    """Records matching the query, up to `limit`, and the total PubMed reports."""
    try:
        result = _esearch(query, retmax=min(limit, 9999))
        total = int(result.get("count", 0))
        id_list = result.get("idlist", [])
        records = {}
        # efetch returns full records including abstracts; esummary does not.
        for start in range(0, len(id_list), 200):
            fetch = requests.post(
                f"{EUTILS_BASE}/efetch.fcgi",
                data=_eutils_params(id=",".join(id_list[start : start + 200]), retmode="xml"),
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            fetch.raise_for_status()
            for article in ET.fromstring(fetch.content).findall("PubmedArticle"):
                record = _parse_article(article)
                if record:
                    records[record["id"]] = record
    except (requests.RequestException, ValueError, ET.ParseError) as exc:
        logger.warning("PubMed search failed", exc_info=True)
        raise SearchError("PubMed search failed. Please try again shortly.") from exc
    return [records[pmid] for pmid in id_list if pmid in records], total


def search_pubmed(query: str, max_results: int = 50) -> list[dict]:
    return pubmed_search(query, max_results)[0]


def _esearch(term: str, retmax: int, sort: str | None = None) -> dict:
    params = _eutils_params(term=term, retmode="json", retmax=retmax)
    if sort:
        params["sort"] = sort
    response = requests.get(f"{EUTILS_BASE}/esearch.fcgi", params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json().get("esearchresult", {})


def count_pubmed(query: str) -> int:
    """How many PubMed records match the query."""
    try:
        return int(_esearch(query, retmax=0).get("count", 0))
    except (requests.RequestException, ValueError) as exc:
        logger.warning("PubMed count failed", exc_info=True)
        raise SearchError("PubMed search failed. Please try again shortly.") from exc


def find_pubmed_reviews(query: str, limit: int = 8) -> list[dict]:
    """The most recent PubMed records in the systematic review subset that match the query."""
    try:
        ids = _esearch(f"({query}) AND systematic[sb]", retmax=limit, sort="pub_date").get("idlist", [])
        if not ids:
            return []
        summary = requests.get(
            f"{EUTILS_BASE}/esummary.fcgi",
            params=_eutils_params(id=",".join(ids), retmode="json"),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        summary.raise_for_status()
        result = summary.json().get("result", {})
    except (requests.RequestException, ValueError) as exc:
        logger.warning("PubMed review search failed", exc_info=True)
        raise SearchError("PubMed search failed. Please try again shortly.") from exc

    reviews = []
    for pmid in ids:
        item = result.get(pmid) or {}
        doi = next((a.get("value", "") for a in item.get("articleids", []) if a.get("idtype") == "doi"), "")
        reviews.append(
            {
                "id": pmid,
                "source": "PubMed",
                "title": item.get("title") or "No Title",
                "year": (item.get("pubdate") or "")[:4],
                "venue": item.get("fulljournalname") or item.get("source") or "",
                "doi": doi,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            }
        )
    return reviews
