"""Parsers for exported search results: RIS, MEDLINE (PubMed .nbib), BibTeX, EndNote XML, Web of Science tagged text,
and CSV. Every format becomes the same record shape; entries without a title are counted and skipped."""

import csv
import io
import re
from collections.abc import Callable
from dataclasses import dataclass

from defusedxml import ElementTree as SafeElementTree
from defusedxml.common import DefusedXmlException

SUPPORTED_FORMATS = "RIS, MEDLINE (.nbib), BibTeX, EndNote XML, Web of Science, or CSV"


class ImportFormatError(Exception):
    """The file couldn't be read. The message is safe to show users."""


@dataclass
class ParsedFile:
    format: str
    records: list[dict]
    # Entries with no title, which can't be screened.
    skipped: int


RawEntry = dict[str, list[str]]


def normalize_doi(value: str) -> str:
    doi = value.strip()
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", doi, flags=re.IGNORECASE)
    return doi.strip().rstrip(".")


def _first(entry: RawEntry, *tags: str) -> str:
    for tag in tags:
        for value in entry.get(tag, []):
            if value.strip():
                return value.strip()
    return ""


def _all(entry: RawEntry, *tags: str) -> list[str]:
    return [value.strip() for tag in tags for value in entry.get(tag, []) if value.strip()]


def _year(text: str) -> str:
    match = re.search(r"(1[5-9]|20)\d{2}", text)
    return match.group(0) if match else ""


def _record(
    title: str, authors: list[str], year: str, venue: str, doi: str, abstract: str, url: str, identifiers: dict
) -> dict:
    return {
        "title": " ".join(title.split())[:2000],
        "authors": "; ".join(authors)[:5000],
        "year": _year(year),
        "venue": " ".join(venue.split())[:1000],
        "doi": normalize_doi(doi)[:255],
        "abstract": " ".join(abstract.split())[:50_000],
        "url": url.strip()[:1000],
        "identifiers": {name: value.strip() for name, value in identifiers.items() if value and value.strip()},
    }


def _tagged_entries(
    text: str, line_pattern: re.Pattern[str], start_tags: set[str], end_tags: set[str]
) -> list[RawEntry]:
    """Parse tagged formats where each line starts with a tag and continuation lines are indented."""
    entries: list[RawEntry] = []
    current: RawEntry = {}
    last_tag = ""
    for line in text.splitlines():
        match = line_pattern.match(line)
        if match:
            tag, value = match.group(1), match.group(2)
            if tag in end_tags:
                if current:
                    entries.append(current)
                current, last_tag = {}, ""
                continue
            if tag in start_tags and current:
                entries.append(current)
                current = {}
            current.setdefault(tag, []).append(value.strip())
            last_tag = tag
        elif line.startswith((" ", "\t")) and line.strip() and last_tag:
            current[last_tag].append("\x00" + line.strip())
        elif not line.strip() and "PMID" in start_tags and current:
            # MEDLINE records are separated by blank lines.
            entries.append(current)
            current, last_tag = {}, ""
    if current:
        entries.append(current)
    return [_merge_continuations(entry) for entry in entries]


def _merge_continuations(entry: RawEntry) -> RawEntry:
    """Continuation lines extend the previous value, except for author tags, where each line is another author."""
    merged: RawEntry = {}
    for tag, values in entry.items():
        result: list[str] = []
        for value in values:
            if value.startswith("\x00") and result:
                text = value[1:]
                if tag in {"AU", "AF", "FAU", "A1", "A2"}:
                    result.append(text)
                else:
                    result[-1] = f"{result[-1]} {text}"
            else:
                result.append(value.lstrip("\x00"))
        merged[tag] = result
    return merged


def parse_ris(text: str) -> list[dict]:
    pattern = re.compile(r"^([A-Z][A-Z0-9])  -\s?(.*)$")
    entries = _tagged_entries(text, pattern, start_tags={"TY"}, end_tags={"ER"})
    return [
        _record(
            title=_first(e, "TI", "T1", "CT", "BT"),
            authors=_all(e, "AU", "A1"),
            year=_first(e, "PY", "Y1", "DA"),
            venue=_first(e, "T2", "JO", "JF", "JA", "J2"),
            doi=_first(e, "DO"),
            abstract=" ".join(_all(e, "AB", "N2")),
            url=_first(e, "UR", "L2"),
            identifiers={"accession": _first(e, "AN")},
        )
        for e in entries
    ]


def parse_medline(text: str) -> list[dict]:
    pattern = re.compile(r"^([A-Z]{2,4})\s*-\s(.*)$")
    entries = _tagged_entries(text, pattern, start_tags={"PMID"}, end_tags=set())
    records = []
    for e in entries:
        ids = _all(e, "LID", "AID")
        doi = next((value.removesuffix("[doi]").strip() for value in ids if value.endswith("[doi]")), "")
        pmid = _first(e, "PMID")
        records.append(
            _record(
                title=_first(e, "TI", "BTI"),
                authors=_all(e, "FAU") or _all(e, "AU"),
                year=_first(e, "DP"),
                venue=_first(e, "JT", "TA"),
                doi=doi,
                abstract=" ".join(_all(e, "AB")),
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
                identifiers={"pmid": pmid, "pmcid": _first(e, "PMC")},
            )
        )
    return records


def parse_wos(text: str) -> list[dict]:
    pattern = re.compile(r"^([A-Z][A-Z0-9]) (.*)$")
    entries = _tagged_entries(text, pattern, start_tags={"PT"}, end_tags={"ER", "EF"})
    entries = [e for e in entries if "FN" not in e or len(e) > 2]
    return [
        _record(
            title=_first(e, "TI"),
            authors=_all(e, "AU") or _all(e, "AF"),
            year=_first(e, "PY"),
            venue=_first(e, "SO"),
            doi=_first(e, "DI"),
            abstract=" ".join(_all(e, "AB")),
            url="",
            identifiers={"wos": _first(e, "UT"), "pmid": _first(e, "PM")},
        )
        for e in entries
        if "FN" not in e
    ]


def _read_bibtex_value(body: str, position: int) -> tuple[str, int]:
    while position < len(body) and body[position].isspace():
        position += 1
    if position >= len(body):
        return "", position
    opener = body[position]
    if opener == "{":
        depth, end = 0, position
        while end < len(body):
            if body[end] == "{":
                depth += 1
            elif body[end] == "}":
                depth -= 1
                if depth == 0:
                    break
            end += 1
        return body[position + 1 : end], end + 1
    if opener == '"':
        end = position + 1
        while end < len(body) and not (body[end] == '"' and body[end - 1] != "\\"):
            end += 1
        return body[position + 1 : end], end + 1
    end = position
    while end < len(body) and body[end] not in ",\n}":
        end += 1
    return body[position:end].strip(), end


def _bibtex_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    comma = body.find(",")
    position = comma + 1 if comma != -1 else len(body)
    name_pattern = re.compile(r"\s*,?\s*([A-Za-z][A-Za-z0-9_-]*)\s*=\s*")
    while position < len(body):
        match = name_pattern.match(body, position)
        if not match:
            break
        value, position = _read_bibtex_value(body, match.end())
        fields[match.group(1).lower()] = re.sub(r"[{}]", "", value).strip()
    return fields


def parse_bibtex(text: str) -> list[dict]:
    records = []
    position = 0
    while (at := text.find("@", position)) != -1:
        brace = text.find("{", at)
        if brace == -1:
            break
        entry_type = text[at + 1 : brace].strip().lower()
        depth, end = 0, brace
        while end < len(text):
            if text[end] == "{":
                depth += 1
            elif text[end] == "}":
                depth -= 1
                if depth == 0:
                    break
            end += 1
        body, position = text[brace + 1 : end], end + 1
        if entry_type in {"comment", "string", "preamble"}:
            continue
        fields = _bibtex_fields(body)
        records.append(
            _record(
                title=fields.get("title", ""),
                authors=[a.strip() for a in re.split(r"\s+and\s+", fields.get("author", "")) if a.strip()],
                year=fields.get("year") or fields.get("date", ""),
                venue=fields.get("journal") or fields.get("booktitle") or fields.get("journaltitle", ""),
                doi=fields.get("doi", ""),
                abstract=fields.get("abstract", ""),
                url=fields.get("url", ""),
                identifiers={"pmid": fields.get("pmid", "")},
            )
        )
    return records


def parse_endnote_xml(text: str) -> list[dict]:
    try:
        root = SafeElementTree.fromstring(text.encode())
    except (DefusedXmlException, SafeElementTree.ParseError) as exc:
        raise ImportFormatError("The EndNote XML file couldn't be read safely.") from exc

    def find_text(record, path: str) -> str:
        element = record.find(path)
        return " ".join("".join(element.itertext()).split()) if element is not None else ""

    records = []
    for record in root.iter("record"):
        records.append(
            _record(
                title=find_text(record, "titles/title"),
                authors=[
                    " ".join("".join(a.itertext()).split()) for a in record.findall("contributors/authors/author")
                ],
                year=find_text(record, "dates/year"),
                venue=find_text(record, "titles/secondary-title") or find_text(record, "periodical/full-title"),
                doi=find_text(record, "electronic-resource-num"),
                abstract=find_text(record, "abstract"),
                url=find_text(record, "urls/related-urls/url"),
                identifiers={"accession": find_text(record, "accession-num")},
            )
        )
    return records


_CSV_COLUMNS = {
    "title": ("title", "article title", "document title", "primary title"),
    "authors": ("authors", "author", "author(s)", "author full names"),
    "year": ("year", "publication year", "pubyear", "py", "date"),
    "venue": ("venue", "journal", "source title", "source", "publication title", "journal/book"),
    "doi": ("doi",),
    "abstract": ("abstract", "abstract note"),
    "pmid": ("pmid", "pubmed id"),
    "url": ("url", "link"),
}


def parse_csv(text: str) -> list[dict]:
    try:
        dialect = csv.Sniffer().sniff(text[:5000], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = {name.strip().lower(): name for name in reader.fieldnames or []}

    def column(row: dict, field_name: str) -> str:
        for alias in _CSV_COLUMNS[field_name]:
            if alias in headers:
                return (row.get(headers[alias]) or "").strip()
        return ""

    if not any(alias in headers for alias in _CSV_COLUMNS["title"]):
        raise ImportFormatError("The CSV file needs a Title column.")
    return [
        _record(
            title=column(row, "title"),
            authors=[a.strip() for a in re.split(r";", column(row, "authors")) if a.strip()],
            year=column(row, "year"),
            venue=column(row, "venue"),
            doi=column(row, "doi"),
            abstract=column(row, "abstract"),
            url=column(row, "url"),
            identifiers={"pmid": column(row, "pmid")},
        )
        for row in reader
    ]


PARSERS: dict[str, Callable[[str], list[dict]]] = {
    "ris": parse_ris,
    "medline": parse_medline,
    "bibtex": parse_bibtex,
    "endnote_xml": parse_endnote_xml,
    "wos": parse_wos,
    "csv": parse_csv,
}


def detect_format(file_name: str, text: str) -> str:
    head = text.lstrip()[:4000]
    extension = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    if re.match(r"^TY  -", head):
        return "ris"
    if re.search(r"^PMID- ", head, re.MULTILINE):
        return "medline"
    if head.startswith("@"):
        return "bibtex"
    if head.startswith("<"):
        return "endnote_xml"
    if re.match(r"^(FN |VR |PT )", head):
        return "wos"
    by_extension = {"ris": "ris", "nbib": "medline", "bib": "bibtex", "xml": "endnote_xml", "ciw": "wos", "csv": "csv"}
    if extension in by_extension:
        return by_extension[extension]
    if head and "," in head.splitlines()[0]:
        return "csv"
    raise ImportFormatError(f"The file format wasn't recognized. Use {SUPPORTED_FORMATS}.")


def parse_records(file_name: str, content: bytes) -> ParsedFile:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    file_format = detect_format(file_name, text)
    try:
        parsed = PARSERS[file_format](text)
    except ImportFormatError:
        raise
    except (ValueError, csv.Error) as exc:
        raise ImportFormatError(f"The {file_format.upper()} file couldn't be read.") from exc
    records = [record for record in parsed if record["title"]]
    return ParsedFile(format=file_format, records=records, skipped=len(parsed) - len(records))
