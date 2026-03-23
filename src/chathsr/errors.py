class ChathsrError(Exception):
    """Base application error."""


class ParseError(ChathsrError):
    """Raised when required ArcaLive HTML could not be parsed."""


class CrawlBlockedError(ChathsrError):
    """Raised when ArcaLive blocks the current browser session."""


class EmbeddingSpaceMismatchError(ChathsrError):
    """Raised when existing vectors use a different embedding space."""


class StorageStateError(ChathsrError):
    """Raised when a Playwright storage_state.json file is missing or invalid."""
