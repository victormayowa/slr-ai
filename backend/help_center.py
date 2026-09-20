"""The product documentation (docs/*.md) split into sections and searched, so the help assistant answers only from
what the documentation says and cites the sections it used."""

import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from active_learning import tokens

# A blank HELP_DOCS_DIR (as in .env.example) means the repository's docs folder.
DOCS_DIR = Path(os.getenv("HELP_DOCS_DIR") or Path(__file__).resolve().parent.parent / "docs")
# Internal planning documents aren't product documentation.
EXCLUDED = {"roadmap.md"}
MIN_SCORE = 1.0


@dataclass(frozen=True)
class HelpSection:
    id: str
    page: str
    page_title: str
    heading: str
    text: str

    @property
    def anchor(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.heading.lower()).strip("-")


_cache: tuple[float, list[HelpSection]] | None = None


def _add_section(sections: list[HelpSection], path: Path, page_title: str, heading: str, lines: list[str]) -> None:
    """Append the lines gathered since the last heading, if they hold anything."""
    text = "\n".join(lines).strip()
    if text:
        sections.append(
            HelpSection(f"{path.stem}-{len(sections) + 1}", path.name, page_title, heading or page_title, text)
        )


def load_sections() -> list[HelpSection]:
    global _cache
    files = sorted(p for p in DOCS_DIR.glob("*.md") if p.name not in EXCLUDED) if DOCS_DIR.is_dir() else []
    stamp = max((p.stat().st_mtime for p in files), default=0.0)
    if _cache is not None and _cache[0] == stamp:
        return _cache[1]
    sections: list[HelpSection] = []
    for path in files:
        page_title = path.stem.replace("-", " ").capitalize()
        heading = ""
        lines: list[str] = []
        for line in path.read_text().splitlines():
            match = re.match(r"^(#{1,3})\s+(.*)$", line)
            if match:
                if match.group(1) == "#":
                    page_title = match.group(2).strip()
                _add_section(sections, path, page_title, heading, lines)
                heading, lines = match.group(2).strip(), []
            else:
                lines.append(line)
        _add_section(sections, path, page_title, heading, lines)
    _cache = (stamp, sections)
    return sections


def search(query: str, limit: int = 6) -> list[HelpSection]:
    """Sections ranked by BM25 over words and word pairs in their page title, heading, and text."""
    sections = load_sections()
    documents = [Counter(tokens(f"{s.page_title} {s.heading} {s.heading} {s.text}")) for s in sections]
    if not documents:
        return []
    average = sum(sum(d.values()) for d in documents) / len(documents)
    frequency: Counter[str] = Counter()
    for document in documents:
        frequency.update(document.keys())
    scored = []
    for section, document in zip(sections, documents, strict=True):
        length = sum(document.values())
        score = 0.0
        for term in set(tokens(query)):
            if term not in document:
                continue
            idf = math.log(1 + (len(documents) - frequency[term] + 0.5) / (frequency[term] + 0.5))
            count = document[term]
            score += idf * count * 2.2 / (count + 1.2 * (0.25 + 0.75 * length / average))
        if score >= MIN_SCORE:
            scored.append((score, section))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [section for _, section in scored[:limit]]


def section_out(section: HelpSection) -> dict[str, str]:
    return {
        "id": section.id,
        "page": section.page,
        "title": section.page_title,
        "heading": section.heading,
        "anchor": section.anchor,
    }
