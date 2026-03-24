from __future__ import annotations

from dataclasses import dataclass
from http.cookiejar import Cookie, CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPCookieProcessor, OpenerDirector, Request, build_opener

from chathsr.config import Settings
from chathsr.errors import CrawlBlockedError, TransportError
from chathsr.session_state import detect_and_normalize_session_payload, load_json_file


CHALLENGE_MARKERS = (
    "Just a moment...",
    "Enable JavaScript and cookies to continue",
)


@dataclass(slots=True)
class _UrllibClient:
    opener: OpenerDirector
    cookie_jar: CookieJar


class CustomHTTPTransport:
    """Repo-local HTTP transport skeleton that you can extend in-place."""

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
        self._client: _UrllibClient | None = None

    def __enter__(self) -> CustomHTTPTransport:
        self._client = self.build_client()
        self.load_cookies(self._client)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
        return None

    def build_client(self) -> _UrllibClient:
        """
        Create the default HTTP client.

        Replace this whole method if you want to use a different client library.
        The only contract that matters to the crawler is: `fetch(url) -> html`.
        """
        cookie_jar = CookieJar()
        opener = build_opener(HTTPCookieProcessor(cookie_jar))
        return _UrllibClient(opener=opener, cookie_jar=cookie_jar)

    def load_cookies(self, client: _UrllibClient) -> None:
        """
        Load cookies from the configured session file into the client.

        By default this reads `PLAYWRIGHT_STORAGE_STATE_PATH` and supports either
        Playwright storage state or the same browser-cookie JSON shape accepted by
        `rag import-state`.

        If you want a different cookie source, override this method and keep the
        rest of the transport unchanged.
        """
        cookie_path = self.settings.playwright_storage_state_path
        if not cookie_path.exists():
            # Leave the client empty by default so the user can choose to rely on
            # direct overrides in this file without forcing a session file.
            return

        payload = load_json_file(cookie_path)
        storage_state, _detected_format = detect_and_normalize_session_payload(payload)
        for raw_cookie in storage_state["cookies"]:
            client.cookie_jar.set_cookie(self._cookie_from_payload(raw_cookie))

        # Customization point:
        # Add any repo-local cookie normalization that your own client needs here.
        # Example: drop stale cookies, rewrite domains, or merge multiple sources.

    def build_headers(self, url: str) -> dict[str, str]:
        """
        Build request headers for a single fetch.

        These defaults are intentionally conservative. If your own collector needs
        extra headers, per-URL Referer changes, or other request shaping, this is
        the place to edit.
        """
        parsed = urlparse(url)
        referer = self.settings.board_url
        if parsed.path.startswith("/u/"):
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
        """
        Fetch a page and return decoded HTML.

        This is the main method you will most likely customize. The current
        version is a generic HTTP fetcher with cookie support and simple
        challenge-page detection. It does not try to bypass blocked responses.
        """
        client = self._require_client()
        request = Request(url, headers=self.build_headers(url))

        try:
            with client.opener.open(request, timeout=60) as response:
                raw_body = response.read()
                html = self._decode_response(response, raw_body)
        except HTTPError as exc:
            raw_body = exc.read()
            html = self._decode_response(exc, raw_body)
            if self._looks_blocked(html):
                raise CrawlBlockedError(self._blocked_message()) from exc
            raise TransportError(f"HTTP {exc.code} while fetching {url}") from exc
        except URLError as exc:
            raise TransportError(f"Network error while fetching {url}: {exc.reason}") from exc

        if self._looks_blocked(html):
            raise CrawlBlockedError(self._blocked_message())
        return html

    def close(self) -> None:
        self._client = None

    def _require_client(self) -> _UrllibClient:
        if self._client is None:
            raise TransportError(
                "The custom HTTP transport has not been initialized. Use it via "
                "a crawler command or enter it as a context manager first."
            )
        return self._client

    def _decode_response(self, response, raw_body: bytes) -> str:
        charset = None
        headers = getattr(response, "headers", None)
        if headers is not None and hasattr(headers, "get_content_charset"):
            charset = headers.get_content_charset()
        if not charset:
            charset = "utf-8"
        return raw_body.decode(charset, errors="replace")

    def _looks_blocked(self, html: str) -> bool:
        lowered = html.lower()
        if any(marker.lower() in lowered for marker in CHALLENGE_MARKERS):
            return True

        # Customization point:
        # If your own client sees other site-specific block or login markers,
        # add them here so the crawler can fail fast with a clear message.
        return False

    def _blocked_message(self) -> str:
        return (
            "The `custom-http` transport received a blocked or challenge page. "
            "Update the repo-local request flow in `src/chathsr/custom_transport.py` "
            "or refresh the cookies/state file used by that transport."
        )

    def _cookie_from_payload(self, raw_cookie: dict[str, object]) -> Cookie:
        domain = str(raw_cookie["domain"])
        path = str(raw_cookie.get("path") or "/")
        expires = raw_cookie.get("expires")
        expires_value = int(float(expires)) if expires not in (None, "") else None
        return Cookie(
            version=0,
            name=str(raw_cookie["name"]),
            value=str(raw_cookie["value"]),
            port=None,
            port_specified=False,
            domain=domain,
            domain_specified=bool(domain),
            domain_initial_dot=domain.startswith("."),
            path=path,
            path_specified=True,
            secure=bool(raw_cookie.get("secure", False)),
            expires=expires_value,
            discard=expires_value is None,
            comment=None,
            comment_url=None,
            rest={
                "HttpOnly": bool(raw_cookie.get("httpOnly", False)),
                "SameSite": str(raw_cookie.get("sameSite", "Lax")),
            },
            rfc2109=False,
        )
