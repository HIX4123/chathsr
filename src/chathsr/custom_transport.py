from __future__ import annotations

from requests.exceptions import HTTPError, RequestException

import cloudscraper

from chathsr.config import Settings
from chathsr.errors import CrawlBlockedError, TransportError
from chathsr.session_state import detect_and_normalize_session_payload, load_json_file


CHALLENGE_MARKERS = (
    "Just a moment...",
    "Enable JavaScript and cookies to continue",
)


class CustomHTTPTransport:
    """cloudscraper 기반 fallback HTTP transport."""

    def __init__(
        self,
        settings: Settings,
        *,
        headless: bool = True,
        force_persistent: bool = False,
    ) -> None:
        self.settings = settings
        self.headless = headless
        self.force_persistent = force_persistent
        self._client: cloudscraper.CloudScraper | None = None

    def __enter__(self) -> CustomHTTPTransport:
        self._client = self.build_client()
        self.load_cookies(self._client)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
        return None

    def build_client(self) -> cloudscraper.CloudScraper:
        return cloudscraper.create_scraper(
            browser={
                "browser": "chrome",
                "platform": "windows",
                "mobile": False,
            }
        )

    def load_cookies(self, client: cloudscraper.CloudScraper) -> None:
        cookie_path = self.settings.playwright_storage_state_path
        if not cookie_path.exists():
            return

        payload = load_json_file(cookie_path)
        storage_state, _detected_format = detect_and_normalize_session_payload(payload)

        for raw_cookie in storage_state["cookies"]:
            expires = raw_cookie.get("expires")
            expires_value = int(float(expires)) if expires not in (None, "") else None

            client.cookies.set(
                name=str(raw_cookie["name"]),
                value=str(raw_cookie["value"]),
                domain=str(raw_cookie["domain"]),
                path=str(raw_cookie.get("path") or "/"),
                secure=bool(raw_cookie.get("secure", False)),
                expires=expires_value,
            )

    def build_headers(self, url: str) -> dict[str, str]:
        referer = self.settings.board_url
        if "/u/" in url:
            referer = "https://arca.live/"

        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.6",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": referer,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/135.0.0.0 Safari/537.36"
            ),
        }

    def fetch(self, url: str) -> str:
        client = self._require_client()

        try:
            response = client.get(
                url,
                headers=self.build_headers(url),
                timeout=60,
                allow_redirects=True,
            )
            response.raise_for_status()
        except HTTPError as exc:
            response = exc.response
            html = self._response_to_html(response) if response is not None else ""
            if html and self._looks_blocked(html):
                raise CrawlBlockedError(self._blocked_message()) from exc

            status = response.status_code if response is not None else "unknown"
            raise TransportError(f"HTTP {status} while fetching {url}") from exc
        except RequestException as exc:
            response = getattr(exc, "response", None)
            html = self._response_to_html(response) if response is not None else ""
            if html and self._looks_blocked(html):
                raise CrawlBlockedError(self._blocked_message()) from exc
            raise TransportError(f"Network error while fetching {url}: {exc}") from exc

        html = self._response_to_html(response)
        if self._looks_blocked(html):
            raise CrawlBlockedError(self._blocked_message())

        return html

    def close(self) -> None:
        if self._client is not None:
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
        return (
            "The `custom-http` transport received a blocked or challenge page. "
            "Update the repo-local request flow in `src/chathsr/custom_transport.py` "
            "or refresh the cookies/state file used by that transport."
        )
