"""AI drafting and language editing of manuscript sections, and AI-written extras (graphical abstract text, highlights).

Drafts are assembled with evidence markers from the model's own evidence keys and then verified like any text. Edits
must keep every marker and number; any change is reported as a problem on the suggestion. Nothing is applied until an
author accepts it.
"""

from collections import Counter
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

from citations import year_of
from claims import CITATION, EVIDENCE, EvidenceItem, markers, number_matches, numbers_in, parse_number, plain_text
from llm.prompts import LANGUAGE_EDIT_PROMPT, MANUSCRIPT_DRAFT_PROMPT, MANUSCRIPT_EXTRAS_PROMPT
from llm.runner import AIContext, AIResult, complete_structured, complete_text

MAX_FACTS_PER_ITEM = 80
MAX_EVIDENCE_CHARS = 90_000
LANGUAGE_TASKS = {
    "academic_tone": "Make the tone formal and academic without changing the meaning.",
    "grammar": "Correct grammar, spelling, and punctuation only.",
    "journal_style": "Adapt the wording and structure to the journal's style instructions.",
    "plain_language": "Rewrite in plain language that a general reader can follow.",
    "concise": "Shorten the text while keeping every claim.",
}
EXTRA_INSTRUCTIONS = {
    "graphical_abstract": "Write the text for a graphical abstract: at most 60 words, as up to four short items covering "
    "the question, the evidence base, and the main findings with their certainty.",
    "highlights": "Write 3 to 5 highlights for the review, each under 85 characters.",
}


class DraftSentence(BaseModel):
    text: str
    evidence: list[str] = Field(default_factory=list)
    citations: list[int] = Field(default_factory=list)


class DraftBlock(BaseModel):
    heading: str | None = None
    sentences: list[DraftSentence] = Field(default_factory=list)


class DraftOutput(BaseModel):
    blocks: list[DraftBlock] = Field(default_factory=list)


class ItemsOutput(BaseModel):
    items: list[str] = Field(default_factory=list)


def evidence_prompt(catalog: Mapping[str, EvidenceItem]) -> str:
    lines, size = [], 0
    for item in catalog.values():
        line = f"[{item.key}] {item.label}: " + "; ".join(item.facts[:MAX_FACTS_PER_ITEM])
        size += len(line)
        if size > MAX_EVIDENCE_CHARS:
            lines.append("(further evidence omitted for length)")
            break
        lines.append(line)
    return "\n".join(lines)


def references_prompt(references: Mapping[int, Mapping[str, Any]]) -> str:
    def first_author(csl: Mapping[str, Any]) -> str:
        authors = csl.get("author") or []
        return authors[0].get("family", "") if authors else ""

    return (
        "\n".join(
            f"[{ref_id}] {first_author(csl)} {year_of(csl)}. {csl.get('title', '')}"
            for ref_id, csl in references.items()
        )
        or "(no references)"
    )


def assemble_draft(
    output: DraftOutput, catalog: Mapping[str, EvidenceItem], references: Mapping[int, Any]
) -> tuple[str, list[str]]:
    lines: list[str] = []
    problems: list[str] = []
    for block in output.blocks:
        if block.heading:
            lines += [f"## {block.heading.strip().lstrip('#').strip()}", ""]
        sentences = []
        for sentence in block.sentences:
            text = " ".join(EVIDENCE.sub("", CITATION.sub("", sentence.text)).split())
            if not text:
                continue
            unknown = [key for key in sentence.evidence if key not in catalog]
            if unknown:
                problems.append(f"Removed evidence keys that don't exist: {', '.join(unknown)}")
            missing = [str(c) for c in sentence.citations if c not in references]
            if missing:
                problems.append(f"Removed citations of references that don't exist: {', '.join(missing)}")
            keys = [key for key in dict.fromkeys(sentence.evidence) if key in catalog]
            cites = [c for c in dict.fromkeys(sentence.citations) if c in references]
            end = text[-1] if text[-1] in ".!?" else "."
            body = text[:-1] if text[-1] in ".!?" else text
            marker = "".join(f" [#{key}]" for key in keys)
            if cites:
                marker += " [" + "; ".join(f"@{c}" for c in cites) + "]"
            sentences.append(f"{body}{marker}{end}")
        if sentences:
            lines += [" ".join(sentences), ""]
    return "\n".join(lines).strip() + "\n", problems


def preservation_problems(original: str, edited: str) -> list[str]:
    problems = []
    before, after = Counter(markers(original)), Counter(markers(edited))
    if before != after:
        removed = list((before - after).elements())
        added = list((after - before).elements())
        detail = "; ".join(
            filter(
                None, [f"removed {', '.join(removed)}" if removed else "", f"added {', '.join(added)}" if added else ""]
            )
        )
        problems.append(f"Evidence markers, citations, or embeds changed: {detail}")
    numbers_before = Counter(numbers_in(plain_text(original)))
    numbers_after = Counter(numbers_in(plain_text(edited)))
    if numbers_before != numbers_after:
        removed = list((numbers_before - numbers_after).elements())
        added = list((numbers_after - numbers_before).elements())
        detail = "; ".join(
            filter(
                None, [f"removed {', '.join(removed)}" if removed else "", f"added {', '.join(added)}" if added else ""]
            )
        )
        problems.append(f"Numbers changed: {detail}")
    return problems


def unverified_numbers(texts: list[str], catalog: Mapping[str, EvidenceItem]) -> list[str]:
    allowed: set[float] = {95.0, 100.0, 1000.0}
    for item in catalog.values():
        allowed |= item.numbers
    return sorted({t for text in texts for t in numbers_in(text) if not number_matches(parse_number(t), t, allowed)})


async def draft_section(
    ai: AIContext,
    section_title: str,
    guidance: str,
    review: str,
    catalog: Mapping[str, EvidenceItem],
    references: Mapping[int, Mapping[str, Any]],
    current: str,
) -> AIResult[DraftOutput]:
    prompt = MANUSCRIPT_DRAFT_PROMPT.render(
        section=section_title,
        guidance=guidance,
        review=review,
        evidence=evidence_prompt(catalog),
        references=references_prompt(references),
        current=current or "(empty)",
    )
    return await complete_structured(ai, prompt, DraftOutput, max_tokens=8000)


async def edit_language(ai: AIContext, kind: str, text: str, journal_style: str) -> AIResult[str]:
    prompt = LANGUAGE_EDIT_PROMPT.render(
        task=LANGUAGE_TASKS[kind], journal_style=journal_style or "(none given)", text=text
    )
    return await complete_text(ai, prompt, max_tokens=8000)


async def write_extra(
    ai: AIContext, kind: str, review: str, catalog: Mapping[str, EvidenceItem]
) -> AIResult[ItemsOutput]:
    prompt = MANUSCRIPT_EXTRAS_PROMPT.render(
        kind_instruction=EXTRA_INSTRUCTIONS[kind], review=review, evidence=evidence_prompt(catalog)
    )
    return await complete_structured(ai, prompt, ItemsOutput, max_tokens=2000)
