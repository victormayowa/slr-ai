"""Checks that text an AI attributes to a source actually appears in it."""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

from rapidfuzz import fuzz

_PUNCTUATION_VARIANTS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_PUNCTUATION_VARIANTS).lower()
    return re.sub(r"\s+", " ", text).strip()


def quote_is_grounded(quote: str, source: str, min_coverage: float = 0.9) -> bool:
    """True when the quote appears in the source, tolerating differences in case, spacing, and quote marks.

    Models sometimes alter a word or a spelling, so a quote also counts when the source passage it lines up with
    (around its longest exact overlap) contains at least `min_coverage` of the quote's characters, in order.
    """
    normalized_quote = _normalize(quote).strip(" .\"'…")
    normalized_source = _normalize(source)
    if not normalized_quote:
        return False
    if normalized_quote in normalized_source:
        return True

    anchor = SequenceMatcher(None, normalized_quote, normalized_source, autojunk=False).find_longest_match(
        0, len(normalized_quote), 0, len(normalized_source)
    )
    if anchor.size == 0:
        return False
    slack = max(5, len(normalized_quote) // 10)
    window_start = max(0, anchor.b - anchor.a - slack)
    window_end = min(len(normalized_source), anchor.b + len(normalized_quote) - anchor.a + slack)
    passage = normalized_source[window_start:window_end]
    blocks = SequenceMatcher(None, normalized_quote, passage, autojunk=False).get_matching_blocks()
    return sum(block.size for block in blocks) / len(normalized_quote) >= min_coverage


@dataclass(frozen=True)
class Passage:
    """A document span as the AI sees it."""

    id: int
    text: str
    kind: str = "paragraph"
    section: str = ""
    page: int | None = None
    label: str = ""


# Sections read first when a document is too long to send whole.
_PRIORITY_SECTIONS = re.compile(
    r"abstract|summary|method|design|participant|patient|population|intervention|outcome|result|finding|statistic|"
    r"baseline|randomi|allocation|blind|follow|analys|trial registration|eligib",
    re.IGNORECASE,
)


def select_passages(passages: Sequence[Passage], max_chars: int) -> list[Passage]:
    """Passages to send, in document order, within max_chars. References are left out. When the document is too long,
    the title, abstract, tables, and methods and results sections are kept before other passages."""
    candidates = [p for p in passages if p.kind != "reference" and p.text.strip()]
    if sum(len(p.text) + 32 for p in candidates) <= max_chars:
        return candidates

    def priority(passage: Passage) -> int:
        if passage.kind in ("title", "abstract"):
            return 0
        if passage.kind in ("table", "caption"):
            return 1
        if _PRIORITY_SECTIONS.search(passage.section) or passage.kind == "heading":
            return 2
        return 3

    chosen: set[int] = set()
    used = 0
    for passage in sorted(candidates, key=priority):
        cost = len(passage.text) + 32
        if used + cost <= max_chars:
            chosen.add(passage.id)
            used += cost
    return [p for p in candidates if p.id in chosen]


def format_passages(passages: Sequence[Passage]) -> str:
    """One line per passage, "[P<id>] (section, p. N) text", so the AI can cite passages by id."""
    lines = []
    for passage in passages:
        where = ", ".join(part for part in (passage.section, f"p. {passage.page}" if passage.page else "") if part)
        label = f"{passage.label}: " if passage.label else ""
        lines.append(f"[P{passage.id}]{f' ({where})' if where else ''} {label}{passage.text}")
    return "\n".join(lines)


def locate_quote(quote: str, passages: Sequence[Passage], cited_ids: Sequence[int] = ()) -> int | None:
    """The id of the passage that contains the quote: a cited passage if it does, otherwise any passage. None when the
    quote can't be found, which means the AI's evidence is unverified."""
    normalized = _normalize(quote).strip(" .\"'…")
    if not normalized:
        return None
    by_id = {passage.id: passage for passage in passages}
    ordered = [by_id[i] for i in cited_ids if i in by_id] + [p for p in passages if p.id not in set(cited_ids)]
    for passage in ordered:
        if normalized in _normalize(passage.text):
            return passage.id
    for passage in ordered:
        # A fast filter before the exact alignment check, which is slow on long passages.
        if fuzz.partial_ratio(normalized, _normalize(passage.text)) >= 85 and quote_is_grounded(quote, passage.text):
            return passage.id
    return None
