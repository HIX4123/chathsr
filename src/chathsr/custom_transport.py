from __future__ import annotations

import sys

from requests.exceptions import HTTPError, RequestException

import cloudscraper

from chathsr.config import Settings
from chathsr.errors import CrawlBlockedError, TransportError


CHALLENGE_MARKERS = (
    "Just a moment...",
    "Enable JavaScript and cookies to continue",
)


class CustomHTTPTransport:
    """plain cloudscraper GET 기반 fallback HTTP transport."""

    def __init__(
        self,
        settings: Settings,
        *,
        headless: bool = True,
        force_persistent: bool = False,
        verbose: bool = False,
    ) -> None:
        self.settings = settings
        self.headless = headless
        self.force_persistent = force_persistent
        self.verbose = verbose
        self._client: cloudscraper.CloudScraper | None = None

    def __enter__(self) -> CustomHTTPTransport:
        self._emit_verbose("initialize client")
        self._client = self.build_client()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
        return None

    def build_client(self) -> cloudscraper.CloudScraper:
        self._emit_verbose("build cloudscraper client")
        return cloudscraper.create_scraper()

    def fetch(self, url: str) -> str:
        client = self._require_client()
        self._emit_verbose(f"GET {url}")

        try:
            response = client.get(url, timeout=60, allow_redirects=True)
            response.raise_for_status()
        except HTTPError as exc:
            response = exc.response
            html = self._response_to_html(response) if response is not None else ""
            status = response.status_code if response is not None else "unknown"
            self._emit_verbose(f"HTTP {status} {url}")
            if html and self._looks_blocked(html):
                raise CrawlBlockedError(self._blocked_message()) from exc

            raise TransportError(f"HTTP {status} while fetching {url}") from exc
        except RequestException as exc:
            response = getattr(exc, "response", None)
            html = self._response_to_html(response) if response is not None else ""
            if response is not None:
                self._emit_verbose(f"HTTP {response.status_code} {url}")
            if html and self._looks_blocked(html):
                raise CrawlBlockedError(self._blocked_message()) from exc
            raise TransportError(f"Network error while fetching {url}: {exc}") from exc

        html = self._response_to_html(response)
        self._emit_verbose(
            f"OK {url} status={response.status_code} bytes={len(html.encode('utf-8'))}"
        )
        if self._looks_blocked(html):
            raise CrawlBlockedError(self._blocked_message())

        return html

    def close(self) -> None:
        if self._client is not None:
            self._emit_verbose("close client")
            self._client.close()
        self._client = None

    def _require_client(self) -> cloudscraper.CloudScraper:
        if self._client is None:
            raise TransportError(
                "The custom HTTP transport has not been initialized. "
                "Use it via a crawler command or enter it as a context manager first."
            )
        return self._client

    def _response_to_html(self, response) -> str:
        if response is None:
            return ""

        if not response.encoding:
            response.encoding = response.apparent_encoding or "utf-8"

        return response.text

    def _looks_blocked(self, html: str) -> bool:
        lowered = html.lower()
        return any(marker.lower() in lowered for marker in CHALLENGE_MARKERS)

    def _blocked_message(self) -> str:
        return "The `custom-http` transport received a blocked or challenge page."

    def _emit_verbose(self, message: str) -> None:
        if not self.verbose:
            return
        print(f"[custom-http] {message}", file=sys.stderr, flush=True)
