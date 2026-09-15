"""References as CSL-JSON: built from records, Crossref, or PubMed; formatted in Vancouver, AMA, APA 7, or Harvard;
numbered by first citation; and exported as BibTeX, RIS, or CSL-JSON. Other CSL styles are applied by Pandoc on export.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from claims import CITATION, CITATION_ID

BUILT_IN_STYLES = {
    "vancouver": "Vancouver (ICMJE)",
    "ama": "AMA 11th edition",
    "apa": "APA 7th edition",
    "harvard": "Harvard (Cite Them Right)",
}
NUMERIC_STYLES = {"vancouver", "ama"}
CSL_STYLE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,98}$")


def _initials(given: str) -> str:
    return "".join(part[0].upper() for part in re.split(r"[\s.\-]+", given) if part)


def parse_authors(text: str) -> tuple[list[dict[str, str]], bool]:
    """Author names from a record's author string ("Smith J, Doe A, et al." or "Smith, John; Doe, Jane")."""
    et_al = bool(re.search(r"et\.? al", text, re.I))
    text = re.sub(r",?\s*et\.? al\.?", "", text, flags=re.I).strip()
    if not text:
        return [], et_al
    names = [n.strip() for n in (text.split(";") if ";" in text else text.split(","))]
    authors: list[dict[str, str]] = []
    for name in names:
        if not name:
            continue
        if "," in name:
            family, given = (part.strip() for part in name.split(",", 1))
        else:
            parts = name.split()
            if len(parts) == 1:
                family, given = parts[0], ""
            elif re.fullmatch(r"[A-Z]{1,4}\.?", parts[-1]):
                family, given = " ".join(parts[:-1]), parts[-1]
            else:
                family, given = parts[-1], " ".join(parts[:-1])
        authors.append({"family": family, "given": given})
    return authors, et_al


def csl_from_record(reference_id: int, record: Any) -> dict[str, Any]:
    authors, et_al = parse_authors(record.authors or "")
    identifiers = record.identifiers or {}
    csl: dict[str, Any] = {
        "id": str(reference_id),
        "type": "article-journal",
        "title": record.title,
        "author": authors,
        "container-title": record.venue or "",
    }
    if et_al:
        csl["note"] = "et-al"
    if record.year and record.year[:4].isdigit():
        csl["issued"] = {"date-parts": [[int(record.year[:4])]]}
    if record.doi:
        csl["DOI"] = record.doi
    if identifiers.get("pmid"):
        csl["PMID"] = identifiers["pmid"]
    if record.url:
        csl["URL"] = record.url
    return csl


def csl_from_crossref(reference_id: int, message: Mapping[str, Any]) -> dict[str, Any]:
    def first(key: str) -> str:
        value = message.get(key) or [""]
        return value[0] if isinstance(value, list) and value else str(value or "")

    csl: dict[str, Any] = {
        "id": str(reference_id),
        "type": message.get("type", "article-journal").replace("journal-article", "article-journal"),
        "title": first("title"),
        "author": [
            {"family": a.get("family", a.get("name", "")), "given": a.get("given", "")}
            for a in message.get("author", [])
        ],
        "container-title": first("container-title"),
        "DOI": message.get("DOI", ""),
    }
    issued = (message.get("issued") or message.get("published") or {}).get("date-parts")
    if issued and issued[0] and issued[0][0]:
        csl["issued"] = {"date-parts": [issued[0][:3]]}
    for key in ("volume", "issue", "page", "URL"):
        if message.get(key):
            csl[key] = message[key]
    return csl


def csl_from_pubmed(reference_id: int, summary: Mapping[str, Any]) -> dict[str, Any]:
    authors, _ = parse_authors(", ".join(a.get("name", "") for a in summary.get("authors", [])))
    doi = next((i.get("value", "") for i in summary.get("articleids", []) if i.get("idtype") == "doi"), "")
    csl: dict[str, Any] = {
        "id": str(reference_id),
        "type": "article-journal",
        "title": summary.get("title", "").rstrip("."),
        "author": authors,
        "container-title": summary.get("fulljournalname") or summary.get("source", ""),
        "PMID": str(summary.get("uid", "")),
    }
    year = (summary.get("pubdate") or "")[:4]
    if year.isdigit():
        csl["issued"] = {"date-parts": [[int(year)]]}
    if doi:
        csl["DOI"] = doi
    for source, target in (("volume", "volume"), ("issue", "issue"), ("pages", "page")):
        if summary.get(source):
            csl[target] = summary[source]
    return csl


def year_of(csl: Mapping[str, Any]) -> str:
    parts = (csl.get("issued") or {}).get("date-parts") or [[]]
    return str(parts[0][0]) if parts and parts[0] else "n.d."


def _sentence(text: str) -> str:
    text = (text or "").strip()
    return text if not text or text[-1] in ".?!" else f"{text}."


def _names(csl: Mapping[str, Any], style: str) -> str:
    authors = [a for a in csl.get("author", []) if a.get("family") or a.get("literal")]
    et_al = csl.get("note") == "et-al"
    if style in ("vancouver", "ama"):
        formatted = [
            f"{a.get('family', a.get('literal', ''))} {_initials(a.get('given', ''))}".strip() for a in authors
        ]
        limit, shown = (6, 6) if style == "vancouver" else (6, 3)
        if len(formatted) > limit or et_al:
            return ", ".join(formatted[: shown if len(formatted) > limit else len(formatted)]) + ", et al"
        return ", ".join(formatted)
    if style == "apa":
        formatted = [
            f"{a.get('family', '')}, {'. '.join(_initials(a.get('given', '')))}.".replace(", .", "") for a in authors
        ]
        if len(formatted) == 1:
            return formatted[0]
        if len(formatted) > 20:
            return ", ".join(formatted[:19]) + ", . . . " + formatted[-1]
        return ", ".join(formatted[:-1]) + ", & " + formatted[-1] if formatted else ""
    formatted = [
        f"{a.get('family', '')}, {'.'.join(_initials(a.get('given', '')))}.".replace(", .", "") for a in authors
    ]
    if len(formatted) > 3 or et_al:
        return f"{formatted[0]} et al." if formatted else ""
    return " and ".join([", ".join(formatted[:-1]), formatted[-1]]) if len(formatted) > 1 else "".join(formatted)


def format_reference(csl: Mapping[str, Any], style: str) -> str:
    """One bibliography entry. Italics use Markdown asterisks."""
    title = csl.get("title", "")
    journal = csl.get("container-title", "")
    year = year_of(csl)
    volume, issue, page = csl.get("volume", ""), csl.get("issue", ""), csl.get("page", "")
    doi = csl.get("DOI", "")
    names = _names(csl, style)
    if style == "vancouver":
        location = (
            f"{year}"
            + (f";{volume}" if volume else "")
            + (f"({issue})" if issue else "")
            + (f":{page}" if page else "")
        )
        parts = [_sentence(names), _sentence(title), _sentence(journal), _sentence(location)]
        return " ".join(p for p in parts if p) + (f" doi:{doi}" if doi else "")
    if style == "ama":
        location = (
            f"{year}"
            + (f";{volume}" if volume else "")
            + (f"({issue})" if issue else "")
            + (f":{page}" if page else "")
        )
        parts = [_sentence(names), _sentence(title), f"*{journal}*." if journal else "", _sentence(location)]
        return " ".join(p for p in parts if p) + (f" doi:{doi}" if doi else "")
    if style == "apa":
        source = f"*{journal}*" if journal else ""
        if volume:
            source += f", *{volume}*" + (f"({issue})" if issue else "")
        if page:
            source += f", {page}"
        parts = [f"{names} ({year}).", _sentence(title), _sentence(source) if source else ""]
        return " ".join(p for p in parts if p) + (f" https://doi.org/{doi}" if doi else "")
    source = f"*{journal}*" if journal else ""
    if volume:
        source += f", {volume}" + (f"({issue})" if issue else "")
    if page:
        source += f", pp. {page}"
    entry = f"{names} ({year}) '{title}'" + (f", {source}" if source else "") + "."
    return entry + (f" doi:{doi}." if doi else "")


def _surname_label(csl: Mapping[str, Any], style: str) -> str:
    authors = csl.get("author", [])
    joiner = " & " if style == "apa" else " and "
    if not authors:
        return csl.get("title", "Anonymous")[:40]
    if len(authors) == 1 and csl.get("note") != "et-al":
        return authors[0].get("family", "")
    if len(authors) == 2 and csl.get("note") != "et-al":
        return f"{authors[0].get('family', '')}{joiner}{authors[1].get('family', '')}"
    return f"{authors[0].get('family', '')} et al."


def _ranges(numbers: list[int]) -> str:
    numbers = sorted(set(numbers))
    groups: list[str] = []
    start = previous = numbers[0]
    for number in numbers[1:] + [None]:  # type: ignore[list-item]
        if number is not None and number == previous + 1:
            previous = number
            continue
        groups.append(
            str(start)
            if start == previous
            else f"{start}–{previous}"
            if previous - start > 1
            else f"{start},{previous}"
        )
        if number is not None:
            start = previous = number
    return ",".join(groups)


@dataclass
class RenderedCitations:
    texts: list[str]
    bibliography: list[tuple[int | None, int, str]]
    order: list[int]


def render(texts: Sequence[str], references: Mapping[int, Mapping[str, Any]], style: str) -> RenderedCitations:
    """Replace citation markers with in-text citations and build the reference list (only cited references)."""
    order: list[int] = []
    for text in texts:
        for match in CITATION.findall(text):
            for ref_id in CITATION_ID.findall(match):
                if int(ref_id) in references and int(ref_id) not in order:
                    order.append(int(ref_id))
    number = {ref_id: i for i, ref_id in enumerate(order, start=1)}

    def replace(match: re.Match[str]) -> str:
        ids = [int(i) for i in CITATION_ID.findall(match.group(0)) if int(i) in references]
        if not ids:
            return ""
        if style == "vancouver":
            return f"[{_ranges([number[i] for i in ids])}]"
        if style == "ama":
            return f"^{_ranges([number[i] for i in ids])}^"
        comma = ", " if style == "apa" else ", "
        return (
            "(" + "; ".join(f"{_surname_label(references[i], style)}{comma}{year_of(references[i])}" for i in ids) + ")"
        )

    rendered = [
        re.sub(r"\s*" + CITATION.pattern, lambda m: (" " if style != "ama" else "") + replace(m), t) for t in texts
    ]
    if style in NUMERIC_STYLES:
        bibliography: list[tuple[int | None, int, str]] = [
            (number[i], i, format_reference(references[i], style)) for i in order
        ]
    else:
        entries = sorted(order, key=lambda i: (_names(references[i], style).casefold(), year_of(references[i])))
        bibliography = [(None, i, format_reference(references[i], style)) for i in entries]
    return RenderedCitations(rendered, bibliography, order)


def _bibtex_escape(value: str) -> str:
    return re.sub(r"([{}&%$#_])", r"\\\1", value)


def to_bibtex(references: Mapping[int, Mapping[str, Any]]) -> str:
    entries = []
    for ref_id, csl in references.items():
        fields = {
            "author": " and ".join(
                f"{a.get('family', '')}, {a.get('given', '')}".strip(", ") for a in csl.get("author", [])
            ),
            "title": csl.get("title", ""),
            "journal": csl.get("container-title", ""),
            "year": year_of(csl) if year_of(csl) != "n.d." else "",
            "volume": csl.get("volume", ""),
            "number": csl.get("issue", ""),
            "pages": str(csl.get("page", "")).replace("-", "--"),
            "doi": csl.get("DOI", ""),
            "pmid": csl.get("PMID", ""),
            "url": csl.get("URL", ""),
        }
        body = ",\n".join(f"  {k} = {{{_bibtex_escape(str(v))}}}" for k, v in fields.items() if v)
        entries.append(f"@article{{ref{ref_id},\n{body}\n}}")
    return "\n\n".join(entries) + "\n"


def to_ris(references: Mapping[int, Mapping[str, Any]]) -> str:
    lines: list[str] = []
    for csl in references.values():
        lines.append("TY  - JOUR")
        lines += [f"AU  - {a.get('family', '')}, {a.get('given', '')}".rstrip(", ") for a in csl.get("author", [])]
        for tag, value in (
            ("TI", csl.get("title", "")),
            ("JO", csl.get("container-title", "")),
            ("PY", year_of(csl) if year_of(csl) != "n.d." else ""),
            ("VL", csl.get("volume", "")),
            ("IS", csl.get("issue", "")),
            ("SP", csl.get("page", "")),
            ("DO", csl.get("DOI", "")),
            ("UR", csl.get("URL", "")),
            ("AN", csl.get("PMID", "")),
        ):
            if value:
                lines.append(f"{tag}  - {value}")
        lines.append("ER  - ")
    return "\n".join(lines) + "\n"


def zotero_item(csl: Mapping[str, Any]) -> dict[str, Any]:
    """A Zotero API journal article from CSL-JSON."""
    return {
        "itemType": "journalArticle",
        "title": csl.get("title", ""),
        "creators": [
            {"creatorType": "author", "lastName": a.get("family", ""), "firstName": a.get("given", "")}
            for a in csl.get("author", [])
        ],
        "publicationTitle": csl.get("container-title", ""),
        "date": year_of(csl) if year_of(csl) != "n.d." else "",
        "volume": str(csl.get("volume", "")),
        "issue": str(csl.get("issue", "")),
        "pages": str(csl.get("page", "")),
        "DOI": csl.get("DOI", ""),
        "url": csl.get("URL", ""),
        "extra": f"PMID: {csl['PMID']}" if csl.get("PMID") else "",
    }
