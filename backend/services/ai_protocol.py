from pydantic import BaseModel

from llm.prompts import PROTOCOL_PROMPT
from llm.runner import AIContext, AIResult, complete_structured


class SearchString(BaseModel):
    database: str
    string: str


class SuggestedCriterion(BaseModel):
    text: str
    # What the criterion restricts; unknown values become "other".
    element: str = "other"


class ProtocolElements(BaseModel):
    inclusion_criteria: list[SuggestedCriterion]
    exclusion_criteria: list[SuggestedCriterion]
    boolean_searches: list[SearchString]


def _clean_criteria(criteria: list[SuggestedCriterion], element_keys: list[str]) -> list[SuggestedCriterion]:
    return [
        SuggestedCriterion(text=c.text.strip(), element=c.element if c.element in element_keys else "other")
        for c in criteria
        if c.text.strip()
    ]


async def generate_protocol_elements(
    ai: AIContext, research_question: str, element_keys: list[str]
) -> AIResult[ProtocolElements]:
    prompt = PROTOCOL_PROMPT.render(research_question=research_question, elements=", ".join(element_keys))
    result = await complete_structured(ai, prompt, ProtocolElements, max_tokens=8000)
    elements = result.value
    cleaned = ProtocolElements(
        inclusion_criteria=_clean_criteria(elements.inclusion_criteria, element_keys),
        exclusion_criteria=_clean_criteria(elements.exclusion_criteria, element_keys),
        boolean_searches=[
            SearchString(database=search.database.strip(), string=search.string.strip())
            for search in elements.boolean_searches
            if search.database.strip() and search.string.strip()
        ],
    )
    return AIResult(cleaned, result.usage)
