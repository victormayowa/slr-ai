"""The help assistant: answers from documentation sections found by help_center.search, citing them by id."""

from pydantic import BaseModel, Field

from help_center import HelpSection
from llm.prompts import FAQ_PROMPT
from llm.runner import AIContext, AIResult, complete_structured


class HelpAnswer(BaseModel):
    answer: str
    citations: list[str] = Field(default_factory=list)


def sections_text(sections: list[HelpSection]) -> str:
    return "\n\n".join(f"[{s.id}] {s.page_title} — {s.heading}\n{s.text}" for s in sections)


async def answer_help(ai: AIContext, query: str, sections: list[HelpSection]) -> AIResult[HelpAnswer]:
    prompt = FAQ_PROMPT.render(query=query, sections=sections_text(sections))
    return await complete_structured(ai, prompt, HelpAnswer, max_tokens=1500)
