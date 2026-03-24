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


class BrowserSessionError(ChathsrError):
    """Raised when the configured local browser session cannot be used."""


class ImportFormatError(ChathsrError):
    """Raised when an imported post export file is missing required fields."""


class TransportError(ChathsrError):
    """Raised when a crawl transport cannot be created or used."""


class UnsupportedTransportError(TransportError):
    """Raised when an unknown crawl transport name is requested."""


class CustomTransportNotImplementedError(TransportError):
    """Raised when the repo-local custom HTTP transport is still a placeholder."""


class ProbeError(ChathsrError):
    """Raised when the websocket probe cannot connect or analyze events."""
