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

    return {
        "id": pmid,
        "title": _text(details.find("ArticleTitle")) or "No Title",
        "authors": ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else ""),
        "year": year,
        "source": "PubMed",
        "venue": details.findtext("Journal/Title", default=""),
        "doi": doi,
        "abstract": "\n".join(abstract_parts),
    }


def search_pubmed(query: str, max_results: int = 50) -> list[dict]:
    try:
        search = requests.get(
            f"{EUTILS_BASE}/esearch.fcgi",
            params=_eutils_params(term=query, retmode="json", retmax=max_results),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        search.raise_for_status()
        id_list = search.json().get("esearchresult", {}).get("idlist", [])
        if not id_list:
            return []

        # efetch returns full records including abstracts; esummary does not.
        fetch = requests.post(
            f"{EUTILS_BASE}/efetch.fcgi",
            data=_eutils_params(id=",".join(id_list), retmode="xml"),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        fetch.raise_for_status()
        root = ET.fromstring(fetch.content)
    except (requests.RequestException, ValueError, ET.ParseError) as exc:
        logger.warning("PubMed search failed", exc_info=True)
        raise SearchError("PubMed search failed. Please try again shortly.") from exc

    records = {}
    for article in root.findall("PubmedArticle"):
        record = _parse_article(article)
        if record:
            records[record["id"]] = record
    return [records[pmid] for pmid in id_list if pmid in records]
