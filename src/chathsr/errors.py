class ChathsrError(Exception):
    """Base application error."""


class ParseError(ChathsrError):
    """Raised when required ArcaLive HTML could not be parsed."""


class CrawlBlockedError(ChathsrError):
    """Raised when ArcaLive blocks the current HTTP crawl flow."""


class EmbeddingSpaceMismatchError(ChathsrError):
    """Raised when existing vectors use a different embedding space."""


class ImportFormatError(ChathsrError):
    """Raised when an imported post export file is missing required fields."""


class SyncBatchError(ChathsrError):
    """Raised when a sync batch cannot be created, uploaded, or imported."""


class SyncConfigurationError(SyncBatchError):
    """Raised when required sync settings are missing or invalid."""


class SyncTransportError(SyncBatchError):
    """Raised when a sync transport command fails."""


class TransportError(ChathsrError):
    """Raised when the HTTP crawl transport cannot be created or used."""
