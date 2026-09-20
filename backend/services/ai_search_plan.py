"""AI help for planning the search: which sources to search, and a search string for each agreed database.

The AI only advises on coverage. Whether OmniReview can run a search itself is decided here from the connector
catalogue, never by the model, so a reviewer is never told a database is searchable when it isn't.
"""

import json
from typing import Any

from pydantic import BaseModel

from llm.prompts import SEARCH_DATABASES_PROMPT, SEARCH_STRINGS_PROMPT
from llm.runner import AIContext, AIResult, complete_structured
from search_sources import CONNECTORS, IMPORT_ONLY_SOURCES, connector_for, import_only_source
from services.errors import LLMError

MAX_DATABASES = 10
MAX_QUERY_LENGTH = 8000


class SuggestedDatabase(BaseModel):
    database: str
    reason: str = ""


class DatabasesOutput(BaseModel):
    databases: list[SuggestedDatabase]


def describe_sources() -> str:
    """The sources OmniReview knows, as the prompt sees them. How each is searched stays out of the model's hands."""
    lines = [f"- {connector.label} ({connector.kind})" for connector in CONNECTORS.values()]
    lines += [f"- {source.label} (searched on {source.interface})" for source in IMPORT_ONLY_SOURCES]
    return "\n".join(lines)


def availability(database: str) -> dict[str, Any]:
    """How this database can be searched: from OmniReview, or on its own platform and imported."""
    connector = connector_for(database)
    if connector is not None:
        return {
            "label": connector.label,
            "searchable": True,
            "connector": connector.key,
            "interface": connector.interface,
            "export_hint": None,
            "note": f"OmniReview can run this search through {connector.interface}.",
        }
    import_only = import_only_source(database)
    if import_only is not None:
        return {
            "label": import_only.label,
            "searchable": False,
            "connector": None,
            "interface": import_only.interface,
            "export_hint": import_only.export_hint,
            "note": (
                f"{import_only.label} can't be searched from OmniReview. Run the string on {import_only.interface}, "
                f"export the results as {import_only.export_hint}, and import the file."
            ),
        }
    return {
        "label": database,
        "searchable": False,
        "connector": None,
        "interface": None,
        "export_hint": None,
        "note": (
            "OmniReview has no connector for this source. Run the string on the source itself, export the results, "
            "and import the file."
        ),
    }


async def suggest_search_databases(ai: AIContext, context: dict[str, Any]) -> AIResult[dict[str, Any]]:
    """Suggest which sources this review should search, each with why it suits the question."""
    prompt = SEARCH_DATABASES_PROMPT.render(
        project=json.dumps(context, indent=2, default=str), sources=describe_sources()
    )
    result = await complete_structured(ai, prompt, DatabasesOutput, max_tokens=2500)
    seen: set[str] = set()
    databases = []
    for item in result.value.databases:
        name = item.database.strip()
        if not name:
            continue
        where = availability(name)
        if where["label"].lower() in seen:
            continue
        seen.add(where["label"].lower())
        databases.append({**where, "database": where["label"], "reason": item.reason.strip()})
        if len(databases) >= MAX_DATABASES:
            break
    if not databases:
        error = LLMError(f"{ai.provider.label} suggested no databases. Try again, or add them yourself.")
        error.usage = result.usage
        raise error
    return AIResult({"databases": databases}, result.usage)


class SearchString(BaseModel):
    database: str
    query: str


class SearchStringsOutput(BaseModel):
    searches: list[SearchString]


def describe_databases(databases: list[str]) -> str:
    lines = []
    for database in databases:
        connector = connector_for(database)
        syntax = connector.syntax_note if connector else "the database's own syntax"
        lines.append(f"- {database}: {syntax}")
    return "\n".join(lines)


async def write_search_strings(
    ai: AIContext, context: dict[str, Any], databases: list[str]
) -> AIResult[dict[str, Any]]:
    """Write one search string per database, in that database's syntax."""
    prompt = SEARCH_STRINGS_PROMPT.render(
        project=json.dumps(context, indent=2, default=str), databases=describe_databases(databases)
    )
    result = await complete_structured(ai, prompt, SearchStringsOutput, max_tokens=4000)
    wanted = {database.lower(): database for database in databases}
    strings: dict[str, str] = {}
    for search in result.value.searches:
        database = wanted.get(search.database.strip().lower())
        query = search.query.strip()[:MAX_QUERY_LENGTH]
        if database and query:
            strings.setdefault(database, query)
    if not strings:
        error = LLMError(f"{ai.provider.label} returned no search strings. Try again, or write them yourself.")
        error.usage = result.usage
        raise error
    return AIResult({"searches": strings, "missing": [name for name in databases if name not in strings]}, result.usage)
