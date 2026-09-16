"""Interoperability: exporting records and review data in the formats other tools read, and importing screening
decisions made elsewhere.

Rayyan and Covidence have no open API, so their decisions are exchanged as files. RevMan and GRADEpro import
spreadsheets rather than a published interchange format, so the CSVs here follow their documented column layouts and
are labelled "RevMan-style" and "GRADEpro-style": check them after importing.
"""

import csv
import io
import json
import re
from collections.abc import Sequence
from typing import Any
from xml.sax.saxutils import escape

import citations
import models
from services.record_import import ImportFormatError, _record

RECORD_FORMATS = ("ris", "bibtex", "endnote_xml", "csv", "json")
RECORD_MEDIA_TYPES = {
    "ris": "application/x-research-info-systems",
    "bibtex": "application/x-bibtex",
    "endnote_xml": "application/xml",
    "csv": "text/csv",
    "json": "application/json",
}
RECORD_EXTENSIONS = {"ris": "ris", "bibtex": "bib", "endnote_xml": "xml", "csv": "csv", "json": "json"}
DECISION_LABELS = {"include": "Included", "exclude": "Excluded", "undecided": "Undecided"}


def _csv(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def _references(records: Sequence[models.Record]) -> dict[int, dict[str, Any]]:
    return {record.id: citations.csl_from_record(record.id, record) for record in records}


def record_json(record: models.Record, decision: str | None) -> dict[str, Any]:
    return {
        "id": record.id,
        "title": record.title,
        "authors": record.authors,
        "year": record.year,
        "venue": record.venue,
        "doi": record.doi,
        "abstract": record.abstract,
        "url": record.url,
        "identifiers": record.identifiers,
        "duplicate_of_id": record.duplicate_of_id,
        "decision": decision,
    }


def _endnote_xml(records: Sequence[models.Record]) -> bytes:
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', "<xml><records>"]
    for record in records:
        authors = "".join(
            f"<author>{escape(name.strip())}</author>" for name in record.authors.split(";") if name.strip()
        )
        parts.append(
            "<record>"
            f"<rec-number>{record.id}</rec-number>"
            f"<titles><title>{escape(record.title)}</title>"
            f"<secondary-title>{escape(record.venue)}</secondary-title></titles>"
            f"<contributors><authors>{authors}</authors></contributors>"
            f"<dates><year>{escape(record.year)}</year></dates>"
            f"<abstract>{escape(record.abstract)}</abstract>"
            f"<electronic-resource-num>{escape(record.doi)}</electronic-resource-num>"
            f"<urls><related-urls><url>{escape(record.url)}</url></related-urls></urls>"
            "</record>"
        )
    parts.append("</records></xml>")
    return "".join(parts).encode()


def export_records(records: Sequence[models.Record], fmt: str, decisions: dict[int, str] | None = None) -> bytes:
    """Render records in one of RECORD_FORMATS. `decisions` adds each record's final decision where the format has a
    place for it (CSV and JSON)."""
    decisions = decisions or {}
    if fmt == "ris":
        return citations.to_ris(_references(records)).encode()
    if fmt == "bibtex":
        return citations.to_bibtex(_references(records)).encode()
    if fmt == "endnote_xml":
        return _endnote_xml(records)
    if fmt == "json":
        payload = [record_json(record, decisions.get(record.id)) for record in records]
        return json.dumps(payload, indent=2, default=str).encode()
    if fmt == "csv":
        header = ["id", "title", "authors", "year", "venue", "doi", "abstract", "url", "decision"]
        rows = [
            [
                record.id,
                record.title,
                record.authors,
                record.year,
                record.venue,
                record.doi,
                record.abstract,
                record.url,
                decisions.get(record.id, ""),
            ]
            for record in records
        ]
        return _csv(header, rows)
    raise ValueError(f"Unknown export format: {fmt}")


def rayyan_csv(records: Sequence[models.Record], decisions: dict[int, str], reviewer: str) -> bytes:
    """Rayyan's review export: one inclusion column naming the reviewer, as {"reviewer"=>"Included"}."""
    header = ["key", "title", "authors", "journal", "year", "doi", "abstract", "url", "RAYYAN-INCLUSION"]
    rows = []
    for record in records:
        decision = decisions.get(record.id)
        inclusion = f'{{"{reviewer}"=>"{DECISION_LABELS[decision]}"}}' if decision in DECISION_LABELS else ""
        rows.append(
            [
                record.id,
                record.title,
                record.authors,
                record.venue,
                record.year,
                record.doi,
                record.abstract,
                record.url,
                inclusion,
            ]
        )
    return _csv(header, rows)


def covidence_csv(records: Sequence[models.Record], decisions: dict[int, str], stage: str) -> bytes:
    """Covidence's study export: the reviewed stage and each study's decision."""
    stage_label = "Title and abstract screening" if stage == "title_abstract" else "Full text review"
    header = ["Study Identifier", "Title", "Authors", "Journal", "Published Year", "DOI", "Abstract", "Stage", "Status"]
    rows = [
        [
            record.id,
            record.title,
            record.authors,
            record.venue,
            record.year,
            record.doi,
            record.abstract,
            stage_label,
            DECISION_LABELS.get(decisions.get(record.id, ""), "Undecided"),
        ]
        for record in records
    ]
    return _csv(header, rows)


# --- Imports ---

_RAYYAN_DECISIONS = {"included": "include", "excluded": "exclude", "maybe": "undecided", "undecided": "undecided"}
_COVIDENCE_DECISIONS = {
    "included": "include",
    "yes": "include",
    "excluded": "exclude",
    "no": "exclude",
    "irrelevant": "exclude",
    "maybe": "undecided",
    "undecided": "undecided",
}
_DECISION_COLUMNS = ("rayyan-inclusion", "inclusion", "status", "decision", "screening decision", "reviewer decision")
_KEY_COLUMNS = ("doi", "key", "study identifier", "covidence #", "covidence number", "id", "record id", "pmid")


def _normalize_decision(value: str) -> str | None:
    text = value.strip().lower()
    if not text:
        return None
    # Rayyan writes {"Reviewer"=>"Included"}; take the first quoted label that names a decision.
    for label in re.findall(r'"([^"]+)"', text):
        if label.lower() in _RAYYAN_DECISIONS:
            return _RAYYAN_DECISIONS[label.lower()]
    return _COVIDENCE_DECISIONS.get(text)


def parse_decision_file(content: bytes) -> list[dict[str, str]]:
    """Read a Rayyan or Covidence export into [{"key", "doi", "title", "decision"}] for matching against records."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    headers = {(name or "").strip().lower(): name for name in reader.fieldnames or []}
    decision_column = next((headers[name] for name in _DECISION_COLUMNS if name in headers), None)
    if decision_column is None:
        raise ImportFormatError(
            "The file needs a decision column (Rayyan's RAYYAN-INCLUSION, or Covidence's Status or Decision)."
        )
    title_column = next((headers[name] for name in ("title", "article title") if name in headers), None)
    rows = []
    for row in reader:
        decision = _normalize_decision(row.get(decision_column) or "")
        if decision is None:
            continue
        rows.append(
            {
                "key": next(
                    (str(row.get(headers[name]) or "").strip() for name in _KEY_COLUMNS if name in headers), ""
                ),
                "doi": str(row.get(headers.get("doi", "")) or "").strip(),
                "title": str(row.get(title_column) or "").strip() if title_column else "",
                "decision": decision,
            }
        )
    if not rows:
        raise ImportFormatError("No decisions were found in that file.")
    return rows


def parse_json_records(content: bytes) -> list[dict[str, Any]]:
    """Read records from JSON: either a list, or {"records": [...]}. Fields match the record export."""
    try:
        payload = json.loads(content.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ImportFormatError("The JSON file couldn't be read.") from exc
    items = payload.get("records") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ImportFormatError('The JSON file must hold a list of records, or {"records": [...]}.')
    records = []
    for item in items:
        if not isinstance(item, dict):
            continue
        authors = item.get("authors") or ""
        author_list = authors if isinstance(authors, list) else [a for a in str(authors).split(";") if a.strip()]
        records.append(
            _record(
                title=str(item.get("title") or ""),
                authors=[str(a).strip() for a in author_list],
                year=str(item.get("year") or ""),
                venue=str(item.get("venue") or item.get("journal") or ""),
                doi=str(item.get("doi") or ""),
                abstract=str(item.get("abstract") or ""),
                url=str(item.get("url") or ""),
                identifiers={k: str(v) for k, v in (item.get("identifiers") or {}).items() if v},
            )
        )
    kept = [record for record in records if record["title"]]
    if not kept:
        raise ImportFormatError("No records with a title were found in the JSON file.")
    return kept


def parse_jats_records(content: bytes) -> list[dict[str, Any]]:
    """Read article metadata from JATS XML: one record per <article>, or per <ref> in a reference list."""
    from defusedxml import ElementTree as SafeElementTree
    from defusedxml.common import DefusedXmlException

    try:
        root = SafeElementTree.fromstring(content)
    except (DefusedXmlException, SafeElementTree.ParseError) as exc:
        raise ImportFormatError("The JATS XML file couldn't be read safely.") from exc

    def text_of(node: Any, path: str) -> str:
        found = node.find(path)
        return " ".join("".join(found.itertext()).split()) if found is not None else ""

    def citation_record(node: Any) -> dict[str, Any]:
        authors = [
            f"{text_of(name, 'surname')} {text_of(name, 'given-names')}".strip() for name in node.findall(".//name")
        ]
        doi = next((i.text or "" for i in node.findall(".//pub-id") if i.get("pub-id-type") == "doi"), "")
        pmid = next((i.text or "" for i in node.findall(".//pub-id") if i.get("pub-id-type") == "pmid"), "")
        return _record(
            title=text_of(node, ".//article-title"),
            authors=[a for a in authors if a],
            year=text_of(node, ".//year"),
            venue=text_of(node, ".//source"),
            doi=doi,
            abstract=text_of(node, ".//abstract"),
            url="",
            identifiers={"pmid": pmid},
        )

    articles = root.findall(".//article") or ([root] if root.tag == "article" else [])
    nodes = articles or root.findall(".//ref")
    records = [record for record in (citation_record(node) for node in nodes) if record["title"]]
    if not records:
        raise ImportFormatError("No articles or references with a title were found in the JATS XML.")
    return records


# --- Review data for other synthesis tools ---


def revman_csv(rows: Sequence[dict[str, Any]]) -> bytes:
    """A RevMan-style dichotomous or continuous data sheet: one row per study per comparison."""
    header = [
        "Comparison",
        "Outcome",
        "Study",
        "Year",
        "Events experimental",
        "Total experimental",
        "Events control",
        "Total control",
        "Mean experimental",
        "SD experimental",
        "Mean control",
        "SD control",
        "Risk of bias",
    ]
    return _csv(header, [[row.get(key, "") for key in header] for row in rows])


def gradepro_csv(columns: Sequence[str], table: Sequence[Sequence[str]]) -> bytes:
    """A GRADEpro-style summary of findings sheet, one row per outcome, as rendered for the manuscript."""
    return _csv(columns, table)


def manuscript_jats(
    title: str,
    abstract: str,
    sections: Sequence[tuple[str, str]],
    references: Sequence[dict[str, Any]],
    authors: Sequence[dict[str, str]],
) -> bytes:
    """The manuscript as JATS XML (archiving tag set), for journals and repositories that take it.

    Each author is {"name", "affiliation", "orcid"}; the last word of the name is taken as the surname.
    """
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE article PUBLIC "-//NLM//DTD JATS (Z39.96) Journal Archiving DTD v1.3 20210610//EN" '
        '"JATS-archivearticle1-3.dtd">',
        '<article xmlns:xlink="http://www.w3.org/1999/xlink" article-type="review" dtd-version="1.3">',
        "<front><article-meta>",
        f"<title-group><article-title>{escape(title)}</article-title></title-group>",
    ]
    if authors:
        contributors = []
        for author in authors:
            words = (author.get("name") or "").split()
            surname, given = (words[-1] if words else ""), " ".join(words[:-1])
            affiliation = f"<aff>{escape(author['affiliation'])}</aff>" if author.get("affiliation") else ""
            orcid = (
                f'<contrib-id contrib-id-type="orcid">{escape(author["orcid"])}</contrib-id>'
                if author.get("orcid")
                else ""
            )
            contributors.append(
                '<contrib contrib-type="author">'
                f"{orcid}<name><surname>{escape(surname)}</surname>"
                f"<given-names>{escape(given)}</given-names></name>{affiliation}</contrib>"
            )
        parts.append(f"<contrib-group>{''.join(contributors)}</contrib-group>")
    if abstract:
        paragraphs = "".join(f"<p>{escape(p)}</p>" for p in abstract.split("\n") if p.strip())
        parts.append(f"<abstract>{paragraphs}</abstract>")
    parts.append("</article-meta></front><body>")
    for heading, body in sections:
        paragraphs = "".join(f"<p>{escape(p.strip())}</p>" for p in body.split("\n") if p.strip())
        parts.append(f"<sec><title>{escape(heading)}</title>{paragraphs}</sec>")
    parts.append("</body>")
    if references:
        entries = []
        for position, reference in enumerate(references, start=1):
            authors_xml = "".join(
                f"<name><surname>{escape(author.get('family', ''))}</surname>"
                f"<given-names>{escape(author.get('given', ''))}</given-names></name>"
                for author in reference.get("author", [])
            )
            doi = reference.get("DOI", "")
            parts_xml = (
                f"<article-title>{escape(str(reference.get('title', '')))}</article-title>"
                f"<source>{escape(str(reference.get('container-title', '')))}</source>"
                f"<year>{escape(citations.year_of(reference))}</year>"
            )
            doi_xml = f'<pub-id pub-id-type="doi">{escape(str(doi))}</pub-id>' if doi else ""
            entries.append(
                f'<ref id="ref{position}"><element-citation publication-type="journal">'
                f"{authors_xml}{parts_xml}{doi_xml}</element-citation></ref>"
            )
        parts.append(f"<back><ref-list>{''.join(entries)}</ref-list></back>")
    parts.append("</article>")
    return "".join(parts).encode()
