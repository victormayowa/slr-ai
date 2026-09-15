"""AI suggestions for screening, extraction, appraisal, entity mentions, and narrative synthesis, plus the support
assistant.

Each function returns validated output with its token usage. Quotes the AI attributes to a source are checked against
it: against the record's title and abstract, or against the passages of a full text, where the passage holding the
quote is recorded as evidence. Suggestions whose quotes can't be found are marked unverified.
"""

import json
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from llm.grounding import Passage, format_passages, locate_quote, quote_is_grounded
from llm.prompts import (
    APPRAISAL_PROMPT,
    ENTITY_PROMPT,
    EXTRACTION_PROMPT,
    FAQ_PROMPT,
    SCREENING_PROMPT,
    SYNTHESIS_PROMPT,
)
from llm.runner import AIContext, AIResult, complete_structured, complete_text

MISSING_VALUE = "Missing from AI response"
NOT_REPORTED = "Not Reported"

ROB_TOOL_DOMAINS = {
    "ROB-2": ["D1: Randomization", "D2: Deviations", "D3: Missing Data", "D4: Measurement", "D5: Selection"],
    "ROBINS-I": [
        "D1: Confounding",
        "D2: Selection",
        "D3: Classification",
        "D4: Deviations",
        "D5: Missing Data",
        "D6: Measurement",
        "D7: Reported Result",
    ],
    "Newcastle-Ottawa": ["Selection", "Comparability", "Outcome/Exposure"],
    "QUADAS-2": ["Patient Selection", "Index Test", "Reference Standard", "Flow and Timing"],
    "PROBAST": ["Participants", "Predictors", "Outcome", "Analysis"],
    "PROBAST+AI": ["Participants", "Predictors", "Outcome", "Analysis", "AI Specific"],
}


def _quote_or_none(quote: str | None) -> str | None:
    return (quote or "").strip() or None


# --- Screening ---


@dataclass(frozen=True)
class CriterionPrompt:
    id: int
    kind: str
    text: str


class CriterionJudgmentOutput(BaseModel):
    criterion_id: int
    judgment: Literal["met", "not_met", "unclear"]
    rationale: str = ""
    quote: str | None = None
    passage_id: int | None = None


class EligibilityOutput(BaseModel):
    criteria: list[CriterionJudgmentOutput] = Field(default_factory=list)
    decision: Literal["Include", "Exclude", "Maybe"]
    confidence: float | None = Field(None, ge=0, le=1)
    reasoning: str
    supporting_quote: str | None = None
    supporting_passage_id: int | None = None


@dataclass
class Eligibility:
    decision: str
    reasoning: str
    supporting_quote: str | None
    # None when the AI gave no quote; otherwise whether the quote appears in the source.
    quote_verified: bool | None
    confidence: float | None = None
    criteria_judgments: list[dict] = field(default_factory=list)
    # For full texts: the passage holding the supporting quote.
    supporting_span_id: int | None = None


def _criteria_text(criteria: list[CriterionPrompt]) -> str:
    lines = []
    for criterion in criteria:
        prefix = "Include only if" if criterion.kind == "inclusion" else "Exclude if"
        lines.append(f"[C{criterion.id}] {prefix}: {criterion.text}")
    return "\n".join(lines)


async def evaluate_eligibility(
    ai: AIContext,
    paper_text: str,
    criteria: list[CriterionPrompt],
    passages: list[Passage] | None = None,
) -> AIResult[Eligibility]:
    """Judge each criterion and suggest a decision. With passages, the full text is screened instead of paper_text."""
    full_text = passages is not None
    prompt = SCREENING_PROMPT.render(
        stage="full text" if full_text else "title and abstract",
        criteria=_criteria_text(criteria),
        paper=format_passages(passages) if passages is not None else paper_text,
        passage_instruction=(
            "The paper is split into passages labelled [P<number>]. Set passage_id to the number of the passage each "
            "quote is copied from."
            if full_text
            else "Set every passage_id to null."
        ),
    )
    result = await complete_structured(ai, prompt, EligibilityOutput, max_tokens=4000)
    output = result.value

    def check(quote: str | None, passage_id: int | None) -> tuple[bool | None, int | None]:
        if quote is None:
            return None, None
        if passages is None:
            return quote_is_grounded(quote, paper_text), None
        span_id = locate_quote(quote, passages, [passage_id] if passage_id is not None else [])
        return span_id is not None, span_id

    by_id = {item.criterion_id: item for item in output.criteria}
    judgments = []
    for criterion in criteria:
        item = by_id.get(criterion.id)
        if item is None:
            judgments.append(
                {
                    "criterion_id": criterion.id,
                    "kind": criterion.kind,
                    "text": criterion.text,
                    "judgment": "unclear",
                    "rationale": "Not assessed by the AI",
                    "quote": None,
                    "quote_verified": None,
                    "span_id": None,
                }
            )
            continue
        quote = _quote_or_none(item.quote)
        verified, span_id = check(quote, item.passage_id)
        judgments.append(
            {
                "criterion_id": criterion.id,
                "kind": criterion.kind,
                "text": criterion.text,
                "judgment": item.judgment,
                "rationale": item.rationale.strip(),
                "quote": quote,
                "quote_verified": verified,
                "span_id": span_id,
            }
        )
    quote = _quote_or_none(output.supporting_quote)
    verified, span_id = check(quote, output.supporting_passage_id)
    return AIResult(
        Eligibility(output.decision, output.reasoning.strip(), quote, verified, output.confidence, judgments, span_id),
        result.usage,
    )


# --- Extraction ---


# Parts to report for structured field types (extraction_values.FIELD_TYPES).
COMPONENT_HINTS = {
    "continuous": "components: mean, sd, n",
    "median_iqr": "components: median, q1, q3, minimum, maximum, n",
    "dichotomous": "components: events, total",
    "effect_estimate": "components: estimate, ci_lower, ci_upper, p_value, measure (risk_ratio, odds_ratio, hazard_ratio, mean_difference, standardized_mean_difference, risk_difference, or other)",
    "dta_2x2": "components: tp, fp, fn, tn",
}


@dataclass(frozen=True)
class FieldPrompt:
    id: int
    name: str
    field_type: str
    per_arm: bool = False
    unit: str = ""
    options: tuple[str, ...] = ()
    help_text: str = ""


class ExtractedItem(BaseModel):
    field_id: int
    arm: str | None = None
    not_reported: bool = False
    value: str | int | float | bool | None = None
    components: dict[str, float | int | str | None] | None = None
    unit: str | None = None
    quote: str | None = None
    passage_ids: list[int] = Field(default_factory=list)
    confidence: float | None = Field(None, ge=0, le=1)
    ambiguous: bool = False


class StudyExtractionOutput(BaseModel):
    arms: list[str] = Field(default_factory=list)
    values: list[ExtractedItem]


def _fields_text(fields: list[FieldPrompt]) -> str:
    lines = []
    for item in fields:
        details = [f"type: {item.field_type}", "once per arm" if item.per_arm else "once for the study"]
        if item.unit:
            details.append(f"report in {item.unit} if the paper uses that unit, and give the unit")
        if item.options:
            details.append(f"one of: {'; '.join(item.options)}")
        if item.field_type in COMPONENT_HINTS:
            details.append(COMPONENT_HINTS[item.field_type])
        if item.help_text:
            details.append(item.help_text)
        lines.append(f"[F{item.id}] {item.name} ({'; '.join(details)})")
    return "\n".join(lines)


async def extract_study_data(
    ai: AIContext, passages: list[Passage], abstract_text: str, fields: list[FieldPrompt], arms: list[str]
) -> AIResult[StudyExtractionOutput]:
    """Suggest values for the fields from the study's full-text passages (or its abstract when there's no full text)."""
    paper = format_passages(passages) if passages else abstract_text
    prompt = EXTRACTION_PROMPT.render(
        paper=paper,
        fields=_fields_text(fields),
        arms="; ".join(arms) if arms else "(not yet defined: list the arms or groups the study compares in arms)",
        passage_instruction=(
            "The paper is split into passages labelled [P<number>]; set passage_ids to the passages each quote comes "
            "from."
            if passages
            else "Leave passage_ids empty."
        ),
    )
    return await complete_structured(ai, prompt, StudyExtractionOutput, max_tokens=8000)


# --- Appraisal ---


class DomainJudgment(BaseModel):
    domain: str
    judgment: Literal["Low", "High", "Unclear"]
    rationale: str = ""


class AppraisalOutput(BaseModel):
    domains: list[DomainJudgment]
    overall: Literal["Low Risk", "High Risk", "Some Concerns"]


async def assess_risk_of_bias(ai: AIContext, paper_text: str, tool: str) -> AIResult[dict[str, str]]:
    domains = ROB_TOOL_DOMAINS.get(tool)
    if domains is None:
        raise ValueError(f"Unsupported risk of bias tool: {tool}")

    prompt = APPRAISAL_PROMPT.render(tool=tool, paper=paper_text, domains="\n".join(f"- {d}" for d in domains))
    result = await complete_structured(ai, prompt, AppraisalOutput, max_tokens=4000)
    judged: dict[str, str] = {}
    for item in result.value.domains:
        judged.setdefault(item.domain.strip().casefold(), item.judgment)
    judgments = {domain: judged.get(domain.casefold(), MISSING_VALUE) for domain in domains}
    judgments["Overall"] = result.value.overall
    return AIResult(judgments, result.usage)


# --- Entities ---


class EntityMention(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    entity_type: Literal["condition", "intervention", "drug", "outcome", "population", "test"]
    passage_id: int


class EntityOutput(BaseModel):
    entities: list[EntityMention]


async def suggest_entities(ai: AIContext, passages: list[Passage]) -> AIResult[list[EntityMention]]:
    result = await complete_structured(
        ai, ENTITY_PROMPT.render(paper=format_passages(passages)), EntityOutput, max_tokens=6000
    )
    return AIResult(result.value.entities, result.usage)


# --- Synthesis and support ---


async def generate_narrative_synthesis(ai: AIContext, studies: list[dict]) -> AIResult[str]:
    return await complete_text(ai, SYNTHESIS_PROMPT.render(studies=json.dumps(studies, indent=2)), max_tokens=6000)


async def answer_faq(ai: AIContext, query: str) -> AIResult[str]:
    return await complete_text(ai, FAQ_PROMPT.render(query=query), max_tokens=1500)
