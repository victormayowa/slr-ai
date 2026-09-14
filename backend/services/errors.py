class LLMError(Exception):
    """An AI provider was unavailable or returned unusable output. The message is safe to show users."""


class SearchError(Exception):
    """A literature database search failed. The message is safe to show users."""
