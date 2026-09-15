"""Claims in manuscript text and their verification.

Section text is Markdown with three kinds of marker:
- evidence: ``[#analysis:3]``, ``[#prisma]``, ``[#grade:4]`` link a sentence to recorded results;
- citations: ``[@5]`` or ``[@5; @6]`` cite manuscript references by id;
- embeds, on their own line: ``[[table:sof]]`` or ``[[figure:run:12:forest]]``.

Every sentence needs evidence or a citation, and every number in it must match the linked evidence (allowing for
rounding, per 1000 figures as percentages, and proportions as percentages). Unsupported sentences, or numbers that can
only be checked against a citation, can be acknowledged by a reviewer; wrong numbers, unknown links, and retracted
citations must be fixed.
"""

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

EVIDENCE = re.compile(r"\[#([a-z_]+(?::[A-Za-z0-9_.-]+)*)\]")
CITATION = re.compile(r"\[@\d+(?:\s*;\s*@\d+)*\]")
CITATION_ID = re.compile(r"@(\d+)")
EMBED = re.compile(r"^\[\[(table|figure):([A-Za-z0-9_:.-]+)\]\]$")
NUMBER = re.compile(r"(?<![\w.])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])-?\d+(?:\.\d+)?")
# Names and labels that contain digits but aren't claims.
NOT_NUMBERS = re.compile(
    r"\b(?:PRISMA(?:-[A-Za-z]+)? ?20\d\d|PRESS 2015|RoB ?2|AMSTAR ?2|QUADAS-2|CONSORT 2010|STARD 2015"
    r"|R version \d+(?:\.\d+)*)\b"
    r"|\b(?:Table|Figure|Fig\.|Appendix|Supplementary (?:Table|Figure|File)|Box|Item|Section) ?S?\d+[a-z]?\b"
    r"|\b[A-Za-z]+-\d+\b|\b[IHτ] ?[²2]\b|\btau\^?2\b|\bτ²"
)
ABBREVIATIONS = ("e.g.", "i.e.", "et al.", "vs.", "cf.", "approx.", "Fig.", "No.", "Dr.", "St.")
ALWAYS_ALLOWED = (95.0, 100.0, 1000.0)
# Statuses a reviewer may acknowledge, and statuses that must be fixed.
ACKNOWLEDGEABLE = {"unsupported", "unverifiable_numbers"}
STATUS_ORDER = ("invalid_link", "retracted_citation", "number_mismatch", "unverifiable_numbers", "unsupported")


@dataclass
class EvidenceItem:
    key: str
    label: str
    facts: list[str]
    group: str = ""

    @property
    def numbers(self) -> set[float]:
        return {parse_number(token) for fact in self.facts for token in numbers_in(fact)}


@dataclass
class ReferenceState:
    id: int
    retracted: bool = False


@dataclass
class Sentence:
    paragraph: int
    index: int
    text: str

    @property
    def hash(self) -> str:
        return sentence_hash(self.text)


@dataclass
class SentenceCheck:
    sentence: Sentence
    status: str
    confidence: str | None
    evidence: list[str]
    citations: list[int]
    issues: list[str] = field(default_factory=list)
    acknowledged: bool = False

    def out(self) -> dict[str, Any]:
        return {
            "paragraph": self.sentence.paragraph,
            "index": self.sentence.index,
            "text": self.sentence.text,
            "hash": self.sentence.hash,
            "status": self.status,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "citations": self.citations,
            "issues": self.issues,
            "acknowledged": self.acknowledged,
        }


def sentence_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()


def parse_number(token: str) -> float:
    return float(token.replace(",", "").replace("−", "-"))


def plain_text(text: str) -> str:
    """Text without evidence and citation markers."""
    return re.sub(r"\s+([.,;:])", r"\1", CITATION.sub("", EVIDENCE.sub("", text))).strip()


def numbers_in(text: str) -> list[str]:
    return NUMBER.findall(NOT_NUMBERS.sub(" ", text.replace("−", "-")))


def split_sentences(content: str) -> list[Sentence]:
    """Sentences of prose paragraphs; headings, embeds, and table rows are skipped. List items count as sentences."""
    sentences: list[Sentence] = []
    paragraph = 0
    for block in re.split(r"\n\s*\n", content):
        prose: list[str] = []
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("|") or EMBED.match(stripped):
                continue
            if re.match(r"^([-*+]|\d+\.)\s+", stripped):
                if prose:
                    sentences += _split(" ".join(prose), paragraph, len(sentences))
                    prose = []
                sentences += _split(re.sub(r"^([-*+]|\d+\.)\s+", "", stripped), paragraph, len(sentences))
                continue
            prose.append(stripped)
        if prose:
            sentences += _split(" ".join(prose), paragraph, len(sentences))
        paragraph += 1
    return sentences


def _split(text: str, paragraph: int, start: int) -> list[Sentence]:
    protected = text
    for abbreviation in ABBREVIATIONS:
        protected = protected.replace(abbreviation, abbreviation.replace(".", "․"))
    # Markers written after a sentence's full stop belong to that sentence: "records. [#prisma]" -> "records [#prisma]."
    protected = re.sub(r"([.!?])((?:\s*\[[#@][^\]]+\])+)", r"\2\1", protected)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(\"“]|\[(?![#@]))", protected)
    return [
        Sentence(paragraph, start + i, part.replace("․", ".").strip()) for i, part in enumerate(parts) if part.strip()
    ]


def number_matches(value: float, token: str, allowed: Iterable[float]) -> bool:
    decimals = len(token.split(".")[1]) if "." in token else 0
    for candidate in allowed:
        for variant in (candidate, candidate / 10, candidate * 100):
            if round(abs(variant), decimals) == abs(value):
                return True
    return False


def check_sentence(
    sentence: Sentence,
    catalog: Mapping[str, EvidenceItem],
    references: Mapping[int, ReferenceState],
    acknowledged: bool = False,
) -> SentenceCheck:
    keys = list(dict.fromkeys(EVIDENCE.findall(sentence.text)))
    citations = list(dict.fromkeys(int(i) for m in CITATION.findall(sentence.text) for i in CITATION_ID.findall(m)))
    found: set[str] = set()
    issues: list[str] = []
    unknown = [key for key in keys if key not in catalog]
    if unknown:
        found.add("invalid_link")
        issues.append(f"Unknown evidence: {', '.join(unknown)}")
    missing_refs = [i for i in citations if i not in references]
    if missing_refs:
        found.add("invalid_link")
        issues.append(f"Unknown references: {', '.join(str(i) for i in missing_refs)}")
    retracted = [i for i in citations if i in references and references[i].retracted]
    if retracted:
        found.add("retracted_citation")
        issues.append(f"Cites retracted references: {', '.join(str(i) for i in retracted)}")
    tokens = numbers_in(plain_text(sentence.text))
    allowed = set(ALWAYS_ALLOWED)
    for key in keys:
        if key in catalog:
            allowed |= catalog[key].numbers
    unmatched = [t for t in tokens if not number_matches(parse_number(t), t, allowed)]
    known_keys = [k for k in keys if k in catalog]
    if unmatched and known_keys:
        found.add("number_mismatch")
        issues.append(f"Numbers not found in the linked evidence: {', '.join(unmatched)}")
    elif unmatched:
        found.add("unverifiable_numbers")
        issues.append(f"Numbers can't be checked without linked evidence: {', '.join(unmatched)}")
    if not keys and not citations:
        found.add("unsupported")
        issues.append("No evidence or citation supports this sentence")
    status = next((s for s in STATUS_ORDER if s in found), "verified")
    is_acknowledged = acknowledged and status in ACKNOWLEDGEABLE
    if is_acknowledged:
        confidence = "acknowledged"
    elif status != "verified":
        confidence = None
    elif tokens and known_keys:
        confidence = "high"
    elif known_keys:
        confidence = "medium"
    else:
        confidence = "low"
    return SentenceCheck(sentence, status, confidence, keys, citations, issues, is_acknowledged)


def check_content(
    content: str,
    catalog: Mapping[str, EvidenceItem],
    references: Mapping[int, ReferenceState],
    acknowledged_hashes: set[str],
) -> list[SentenceCheck]:
    return [check_sentence(s, catalog, references, s.hash in acknowledged_hashes) for s in split_sentences(content)]


def unresolved(checks: Iterable[SentenceCheck]) -> list[SentenceCheck]:
    return [c for c in checks if c.status != "verified" and not c.acknowledged]


def embeds(content: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for line in content.splitlines() if (m := EMBED.match(line.strip())) is not None]


def markers(text: str) -> list[str]:
    """Every marker in the text, in order, to check that an edit keeps them."""
    return EVIDENCE.findall(text) + CITATION.findall(text) + [f"{k}:{v}" for k, v in embeds(text)]
