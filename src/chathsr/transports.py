from __future__ import annotations

from typing import Protocol, Self

from chathsr.config import Settings
from chathsr.http_transport import HTTPTransport


class FetchTransport(Protocol):
    def fetch(self, url: str) -> str: ...

    def close(self) -> None: ...

    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type, exc, tb) -> None: ...


def create_transport(
    settings: Settings,
    *,
    verbose: bool = False,
) -> FetchTransport:
    return HTTPTransport(settings, verbose=verbose)
