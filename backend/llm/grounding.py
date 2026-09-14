"""Checks that text an AI attributes to a source actually appears in it."""

import re
import unicodedata
from difflib import SequenceMatcher

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
