"""AI suggestions for screening, extraction, appraisal, and narrative synthesis, plus the support assistant.

Each function returns validated output with its token usage. Quotes the AI attributes to a record are checked against
the record's text, and the result is stored so reviewers can see which suggestions are grounded.
"""

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from llm.grounding import quote_is_grounded
from llm.prompts import APPRAISAL_PROMPT, EXTRACTION_PROMPT, FAQ_PROMPT, SCREENING_PROMPT, SYNTHESIS_PROMPT
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


class EligibilityOutput(BaseModel):
    decision: Literal["Include", "Exclude", "Maybe"]
    reasoning: str
    supporting_quote: str | None = None


@dataclass
class Eligibility:
    decision: str
    reasoning: str
    supporting_quote: str | None
    # None when the AI gave no quote; otherwise whether the quote appears in the record's text.
    quote_verified: bool | None


async def evaluate_eligibility(ai: AIContext, paper_text: str, criteria: str) -> AIResult[Eligibility]:
    result = await complete_structured(
        ai, SCREENING_PROMPT.render(criteria=criteria, paper=paper_text), EligibilityOutput, max_tokens=2000
    )
    output = result.value
    quote = _quote_or_none(output.supporting_quote)
    verified = None if quote is None else quote_is_grounded(quote, paper_text)
    return AIResult(Eligibility(output.decision, output.reasoning.strip(), quote, verified), result.usage)


class ExtractedValue(BaseModel):
    field: str
    value: str | int | float | bool | None = None
    quote: str | None = None


class ExtractionOutput(BaseModel):
    values: list[ExtractedValue]


@dataclass
class ExtractedField:
    field: str
    value: str
    quote: str | None
    # None when there is no reported value to check; otherwise whether the value's quote appears in the text.
    quote_verified: bool | None


async def extract_data_from_paper(
    ai: AIContext, paper_text: str, field_names: list[str]
) -> AIResult[list[ExtractedField]]:
    prompt = EXTRACTION_PROMPT.render(paper=paper_text, fields="\n".join(f"- {name}" for name in field_names))
    result = await complete_structured(ai, prompt, ExtractionOutput, max_tokens=4000)
    by_field: dict[str, ExtractedValue] = {}
    for item in result.value.values:
        by_field.setdefault(item.field.strip().casefold(), item)

    fields = []
    for name in field_names:
        found = by_field.get(name.casefold())
        value = "" if found is None or found.value is None else str(found.value).strip()
        if found is None:
            fields.append(ExtractedField(name, MISSING_VALUE, None, None))
        elif not value or value.casefold() == NOT_REPORTED.casefold():
            fields.append(ExtractedField(name, NOT_REPORTED, None, None))
        else:
            quote = _quote_or_none(found.quote)
            # A reported value without a quote that can be found in the text is unverified.
            verified = quote is not None and quote_is_grounded(quote, paper_text)
            fields.append(ExtractedField(name, value, quote, verified))
    return AIResult(fields, result.usage)


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


async def generate_narrative_synthesis(ai: AIContext, studies: list[dict]) -> AIResult[str]:
    return await complete_text(ai, SYNTHESIS_PROMPT.render(studies=json.dumps(studies, indent=2)), max_tokens=6000)


async def answer_faq(ai: AIContext, query: str) -> AIResult[str]:
    return await complete_text(ai, FAQ_PROMPT.render(query=query), max_tokens=1500)
