"""AI support for publication: reading journal guidelines (every requirement backed by a quote that must be found in
the guidelines), cover letters, splitting reviewer reports into comments (verbatim only), and response letters."""

import re
from typing import Any

from pydantic import BaseModel, Field

from llm.grounding import quote_is_grounded
from llm.prompts import COVER_LETTER_PROMPT, GUIDELINE_PROMPT, RESPONSE_LETTER_PROMPT, REVIEW_COMMENTS_PROMPT
from llm.runner import AIContext, AIResult, complete_structured, complete_text

REQUIREMENT_NAMES = (
    "word_limit",
    "abstract_word_limit",
    "abstract_structure",
    "reference_style",
    "max_references",
    "max_tables",
    "max_figures",
    "figure_formats",
    "figure_resolution_dpi",
    "keywords",
    "highlights",
    "cover_letter",
    "title_page",
    "required_statements",
    "reporting_guideline",
    "data_sharing",
    "preprint_policy",
    "other",
)
MAX_GUIDELINE_CHARS = 150_000
COMMENT_CATEGORIES = ("major", "minor", "editorial", "methods", "statistics", "other")


class RequirementOut(BaseModel):
    name: str
    value: str
    quote: str = ""


class GuidelineOutput(BaseModel):
    requirements: list[RequirementOut] = Field(default_factory=list)


class CommentOut(BaseModel):
    reviewer: str
    number: str = ""
    text: str
    category: str = "other"


class CommentsOutput(BaseModel):
    comments: list[CommentOut] = Field(default_factory=list)


async def read_guideline(ai: AIContext, journal: str, text: str) -> AIResult[GuidelineOutput]:
    prompt = GUIDELINE_PROMPT.render(
        journal=journal, guideline=text[:MAX_GUIDELINE_CHARS], requirement_names=", ".join(REQUIREMENT_NAMES)
    )
    return await complete_structured(ai, prompt, GuidelineOutput, max_tokens=4000)


def grounded_requirements(output: GuidelineOutput, text: str) -> dict[str, Any]:
    """Requirements keyed by name. Several values for one name are joined; a requirement is grounded only when its
    quote is found in the guidelines. Ungrounded requirements are kept but never used by readiness checks."""
    requirements: dict[str, Any] = {}
    for item in output.requirements:
        if item.name not in REQUIREMENT_NAMES or not item.value.strip():
            continue
        grounded = bool(item.quote.strip()) and quote_is_grounded(item.quote, text)
        entry = requirements.setdefault(item.name, {"value": "", "quotes": [], "grounded": True, "source": "ai"})
        entry["value"] = f"{entry['value']}; {item.value.strip()}" if entry["value"] else item.value.strip()
        entry["quotes"].append(item.quote)
        entry["grounded"] = entry["grounded"] and grounded
    return requirements


async def draft_cover_letter(ai: AIContext, journal: str, review: str, findings: str, statements: str) -> AIResult[str]:
    prompt = COVER_LETTER_PROMPT.render(journal=journal, review=review, findings=findings, statements=statements)
    return await complete_text(ai, prompt, max_tokens=2000)


async def split_comments(ai: AIContext, report: str) -> AIResult[CommentsOutput]:
    return await complete_structured(
        ai, REVIEW_COMMENTS_PROMPT.render(comments=report[:MAX_GUIDELINE_CHARS]), CommentsOutput, max_tokens=8000
    )


def grounded_comments(output: CommentsOutput, report: str) -> tuple[list[dict[str, str]], int]:
    """Comments whose text is found in the report, and how many were dropped because they weren't."""
    kept, dropped = [], 0
    for comment in output.comments:
        if not comment.text.strip() or not quote_is_grounded(comment.text, report):
            dropped += 1
            continue
        kept.append(
            {
                "reviewer": comment.reviewer.strip()[:100] or "Reviewer",
                "number": comment.number.strip()[:20],
                "body": comment.text.strip(),
                "category": comment.category if comment.category in COMMENT_CATEGORIES else "other",
            }
        )
    return kept, dropped


REVIEWER_HEADER = re.compile(
    r"^\s*(?:\*\*|#+\s*)?((?:Reviewer|Referee)\s*#?\s*\d+|(?:Associate |Academic |Handling )?Editor(?:'s)?(?: comments)?)\b\s*(?:\*\*)?[:.\-]?\s*$",
    re.I,
)
NUMBERED = re.compile(r"^\s*(?:(?:Comment|Point|Q|Issue)\s*)?#?(\d+(?:\.\d+)*)\s*[.):\-]\s+(.*)$", re.I)


def rule_split_comments(report: str) -> list[dict[str, str]]:
    """Split a report on reviewer headings ("Reviewer 2", "Editor") and numbered points."""
    comments: list[dict[str, str]] = []
    reviewer = "Reviewer 1"
    current: dict[str, str] | None = None
    for line in report.splitlines():
        header = REVIEWER_HEADER.match(line)
        if header:
            reviewer = re.sub(r"\s+", " ", header.group(1)).strip().replace("#", "").replace("  ", " ")
            current = None
            continue
        numbered = NUMBERED.match(line)
        if numbered:
            current = {
                "reviewer": reviewer,
                "number": numbered.group(1),
                "body": numbered.group(2).strip(),
                "category": "",
            }
            comments.append(current)
            continue
        if not line.strip():
            continue
        if current is None:
            current = {
                "reviewer": reviewer,
                "number": str(sum(1 for c in comments if c["reviewer"] == reviewer) + 1),
                "body": line.strip(),
                "category": "",
            }
            comments.append(current)
        else:
            current["body"] = f"{current['body']} {line.strip()}"
    return comments


async def draft_response_letter(ai: AIContext, journal: str, comments: str) -> AIResult[str]:
    return await complete_text(ai, RESPONSE_LETTER_PROMPT.render(journal=journal, comments=comments), max_tokens=8000)
