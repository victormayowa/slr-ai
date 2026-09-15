"""AI suggestions for risk of bias assessments and reporting checklists, grounded in the study's full-text passages.

Suggestions are stored apart from reviewers' answers and never sign anything off.
"""

from pydantic import BaseModel, Field

from appraisal_tools import ANSWER_SETS, JUDGMENT_SETS, Tool
from llm.grounding import Passage, format_passages
from llm.prompts import APPRAISAL_PROMPT, REPORTING_PROMPT
from llm.runner import AIContext, AIResult, complete_structured
from reporting_checklists import STATUSES, Checklist


class SuggestedAnswer(BaseModel):
    question_id: str
    answer: str
    rationale: str = ""
    quote: str | None = None
    passage_id: int | None = None


class SuggestedDomain(BaseModel):
    domain: str
    judgment: str
    rationale: str = ""


class AppraisalOutput(BaseModel):
    answers: list[SuggestedAnswer] = Field(default_factory=list)
    domains: list[SuggestedDomain] = Field(default_factory=list)


def _passage_instruction(passages: list[Passage]) -> str:
    if passages:
        return "The paper is split into passages labelled [P<number>]; set passage_id to the passage each quote is copied from."
    return "Set passage_id to null."


async def suggest_appraisal(
    ai: AIContext, tool: Tool, outcome: str, passages: list[Passage], abstract_text: str
) -> AIResult[AppraisalOutput]:
    questions = []
    domains = []
    for domain in tool.domains:
        for question in domain.questions:
            answers = ", ".join(f"{key} ({label})" for key, label in ANSWER_SETS[question.answers])
            condition = ""
            if question.asked_if:
                joiner = " or " if question.asked_if.mode == "any" else " and "
                condition = f" [only if {joiner.join(question.asked_if.questions)} answered {'/'.join(sorted(question.asked_if.answers))}]"
            questions.append(f"{question.id} ({domain.label}): {question.text}{condition}. Answers: {answers}")
        judgments = ", ".join(f"{key} ({label})" for key, label in JUDGMENT_SETS[domain.judgments])
        domains.append(f"{domain.key}: {domain.label}. Judgments: {judgments}")
    prompt = APPRAISAL_PROMPT.render(
        tool=f"{tool.label}, {tool.version}",
        paper=format_passages(passages) if passages else abstract_text,
        outcome=outcome or "the study as a whole",
        questions="\n".join(questions) or "(this tool records domain judgments only)",
        domains="\n".join(domains),
        passage_instruction=_passage_instruction(passages),
    )
    return await complete_structured(ai, prompt, AppraisalOutput, max_tokens=8000)


class SuggestedItem(BaseModel):
    item_id: str
    status: str
    rationale: str = ""
    quote: str | None = None
    passage_id: int | None = None


class ReportingOutput(BaseModel):
    items: list[SuggestedItem] = Field(default_factory=list)


async def suggest_reporting(
    ai: AIContext, checklist: Checklist, passages: list[Passage], abstract_text: str
) -> AIResult[ReportingOutput]:
    prompt = REPORTING_PROMPT.render(
        checklist=checklist.label,
        paper=format_passages(passages) if passages else abstract_text,
        items="\n".join(f"{item_id} ({section}): {topic}" for item_id, section, topic in checklist.items),
        statuses=", ".join(STATUSES),
        passage_instruction=_passage_instruction(passages),
    )
    return await complete_structured(ai, prompt, ReportingOutput, max_tokens=8000)
