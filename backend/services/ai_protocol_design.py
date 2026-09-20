"""AI suggestions for protocol design: a structured review question, a pre-specified analysis plan, PRISMA-P section
drafts, and a consistency review.

All output is a suggestion for reviewers. Drafts mark missing information with placeholders instead of inventing it.
"""

import json
from typing import Any, Literal

from pydantic import BaseModel

from llm.prompts import (
    ANALYSIS_PLAN_PROMPT,
    CONSISTENCY_PROMPT,
    QUESTION_PROMPT,
    SECTION_DRAFT_PROMPT,
    TOPIC_QUESTIONS_PROMPT,
)
from llm.runner import AIContext, AIResult, complete_structured
from protocol_frameworks import (
    FINER_CRITERIA,
    FRAMEWORKS,
    OUTCOME_PRIORITIES,
    SYNTHESIS_APPROACHES,
    Section,
    describe_frameworks,
)
from services.errors import LLMError

FrameworkKey = Literal["PICO", "PICOS", "PECO", "SPIDER", "PCC"]
FinerKey = Literal["feasible", "interesting", "novel", "ethical", "relevant"]


class ElementText(BaseModel):
    element: str
    text: str


class FinerNote(BaseModel):
    criterion: FinerKey
    note: str


class QuestionOutput(BaseModel):
    framework: FrameworkKey
    question: str
    elements: list[ElementText]
    finer_notes: list[FinerNote] = []


async def structure_question(ai: AIContext, topic: str) -> AIResult[dict[str, Any]]:
    prompt = QUESTION_PROMPT.render(topic=topic, frameworks=describe_frameworks())
    result = await complete_structured(ai, prompt, QuestionOutput, max_tokens=3000)
    output = result.value
    framework = FRAMEWORKS[output.framework]
    given = {item.element: item.text.strip() for item in output.elements}
    finer_keys = {criterion.key for criterion in FINER_CRITERIA}
    return AIResult(
        {
            "framework": output.framework,
            "question": output.question.strip(),
            "elements": {element.key: given.get(element.key, "") for element in framework.elements},
            "finer_notes": {
                note.criterion: note.note.strip()
                for note in output.finer_notes
                if note.criterion in finer_keys and note.note.strip()
            },
        },
        result.usage,
    )


class SectionDraftOutput(BaseModel):
    content: str
    missing_information: list[str] = []


async def draft_protocol_section(ai: AIContext, section: Section, context: dict[str, Any]) -> AIResult[dict[str, Any]]:
    prompt = SECTION_DRAFT_PROMPT.render(
        section_label=section.label,
        prisma_item=section.prisma_p_item,
        guidance=section.guidance,
        project=json.dumps(context, indent=2, default=str),
    )
    result = await complete_structured(ai, prompt, SectionDraftOutput, max_tokens=4000)
    content = result.value.content.strip()
    if not content:
        error = LLMError(f"{ai.provider.label} returned an empty draft")
        error.usage = result.usage
        raise error
    missing = [item.strip() for item in result.value.missing_information if item.strip()]
    return AIResult({"content": content, "missing_information": missing}, result.usage)


# Analysis plan

SynthesisApproach = Literal["meta_analysis", "swim", "narrative", "undecided"]
OutcomePriority = Literal["primary", "secondary", "adverse"]
# What a reviewer can take in at a glance, and what PRISMA-P asks to be pre-specified rather than an exhaustive list.
MAX_OUTCOMES = 10
MAX_ANALYSES = 5


class SuggestedOutcome(BaseModel):
    name: str
    priority: OutcomePriority
    timepoint: str = ""
    measure: str = ""


class SuggestedAnalysis(BaseModel):
    name: str
    rationale: str = ""


class AnalysisPlanOutput(BaseModel):
    synthesis_approach: SynthesisApproach
    outcomes: list[SuggestedOutcome] = []
    subgroups: list[SuggestedAnalysis] = []
    sensitivity_analyses: list[SuggestedAnalysis] = []
    heterogeneity: str = ""


def _named(items: list[SuggestedAnalysis], limit: int) -> list[dict[str, str]]:
    cleaned = [{"name": item.name.strip(), "rationale": item.rationale.strip()} for item in items if item.name.strip()]
    return cleaned[:limit]


async def suggest_analysis_plan(ai: AIContext, context: dict[str, Any]) -> AIResult[dict[str, Any]]:
    """Suggest outcomes, subgroup and sensitivity analyses, and a synthesis approach from the protocol so far.

    The shape matches the analysis plan reviewers edit, so the screen can fill the form from it.
    """
    prompt = ANALYSIS_PLAN_PROMPT.render(
        project=json.dumps(context, indent=2, default=str),
        synthesis_approaches=", ".join(f"{key} ({label})" for key, label in SYNTHESIS_APPROACHES.items()),
        outcome_priorities=", ".join(f"{key} ({label})" for key, label in OUTCOME_PRIORITIES.items()),
    )
    result = await complete_structured(ai, prompt, AnalysisPlanOutput, max_tokens=3000)
    output = result.value
    outcomes = [
        {
            "name": outcome.name.strip(),
            "priority": outcome.priority,
            "timepoint": outcome.timepoint.strip(),
            "measure": outcome.measure.strip(),
        }
        for outcome in output.outcomes
        if outcome.name.strip()
    ][:MAX_OUTCOMES]
    if not outcomes:
        error = LLMError(f"{ai.provider.label} suggested no outcomes. Try again, or write the plan yourself.")
        error.usage = result.usage
        raise error
    return AIResult(
        {
            "synthesis_approach": output.synthesis_approach,
            "outcomes": outcomes,
            "subgroups": _named(output.subgroups, MAX_ANALYSES),
            "sensitivity_analyses": _named(output.sensitivity_analyses, MAX_ANALYSES),
            "heterogeneity": output.heterogeneity.strip(),
        },
        result.usage,
    )


class ConsistencyIssue(BaseModel):
    severity: Literal["error", "warning"]
    message: str
    criterion_ids: list[int] = []
    elements: list[str] = []


class ConsistencyOutput(BaseModel):
    issues: list[ConsistencyIssue]


async def review_protocol_consistency(
    ai: AIContext, context: dict[str, Any], criterion_ids: set[int], element_keys: set[str]
) -> AIResult[dict[str, Any]]:
    prompt = CONSISTENCY_PROMPT.render(project=json.dumps(context, indent=2, default=str))
    result = await complete_structured(ai, prompt, ConsistencyOutput, max_tokens=4000)
    issues = [
        {
            "severity": issue.severity,
            "message": issue.message.strip(),
            # Only ids and keys that exist in this project; anything else was made up.
            "criterion_ids": [criterion_id for criterion_id in issue.criterion_ids if criterion_id in criterion_ids],
            "elements": [key for key in issue.elements if key in element_keys],
        }
        for issue in result.value.issues
        if issue.message.strip()
    ]
    return AIResult({"issues": issues}, result.usage)


GapKind = Literal[
    "no_review_found", "outdated_review", "uncovered_population_or_setting", "conflicting_findings", "other"
]


class TopicQuestion(BaseModel):
    question: str
    framework: FrameworkKey
    gap: GapKind
    rationale: str
    based_on_review_ids: list[str] = []


class TopicQuestionsOutput(BaseModel):
    questions: list[TopicQuestion]
    evidence_limitations: str = ""


async def suggest_topic_questions(
    ai: AIContext, project: dict[str, Any], evidence: dict[str, Any], review_ids: set[str]
) -> AIResult[dict[str, Any]]:
    prompt = TOPIC_QUESTIONS_PROMPT.render(
        project=json.dumps(project, indent=2, default=str), evidence=json.dumps(evidence, indent=2, default=str)
    )
    result = await complete_structured(ai, prompt, TopicQuestionsOutput, max_tokens=4000)
    questions = [
        {
            "question": item.question.strip(),
            "framework": item.framework,
            "gap": item.gap,
            "rationale": item.rationale.strip(),
            # Only reviews that were actually retrieved.
            "based_on_review_ids": [ref for ref in item.based_on_review_ids if ref in review_ids],
        }
        for item in result.value.questions[:5]
        if item.question.strip()
    ]
    return AIResult(
        {"questions": questions, "evidence_limitations": result.value.evidence_limitations.strip()}, result.usage
    )
