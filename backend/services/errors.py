from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm.runner import Usage


class LLMError(Exception):
    """An AI provider was unavailable or returned unusable output. The message is safe to show users."""

    # Tokens and time spent on the attempts made before the failure, when a provider was called.
    usage: "Usage | None" = None


class SearchError(Exception):
    """A literature database search failed. The message is safe to show users."""
