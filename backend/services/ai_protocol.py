from pydantic import BaseModel

from llm.prompts import PROTOCOL_PROMPT
from llm.runner import AIContext, AIResult, complete_structured


class SearchString(BaseModel):
    database: str
    string: str


class ProtocolElements(BaseModel):
    inclusion_criteria: list[str]
    exclusion_criteria: list[str]
    boolean_searches: list[SearchString]


async def generate_protocol_elements(ai: AIContext, research_question: str) -> AIResult[ProtocolElements]:
    result = await complete_structured(
        ai, PROTOCOL_PROMPT.render(research_question=research_question), ProtocolElements, max_tokens=8000
    )
    elements = result.value
    cleaned = ProtocolElements(
        inclusion_criteria=[text.strip() for text in elements.inclusion_criteria if text.strip()],
        exclusion_criteria=[text.strip() for text in elements.exclusion_criteria if text.strip()],
        boolean_searches=[
            SearchString(database=search.database.strip(), string=search.string.strip())
            for search in elements.boolean_searches
            if search.database.strip() and search.string.strip()
        ],
    )
    return AIResult(cleaned, result.usage)
