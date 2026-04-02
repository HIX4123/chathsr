from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import cloudscraper
from requests.cookies import RequestsCookieJar, create_cookie
from requests.exceptions import HTTPError, RequestException

from chathsr.config import Settings
from chathsr.errors import CrawlBlockedError, TransportError


CHALLENGE_MARKERS = (
    "Just a moment...",
    "Enable JavaScript and cookies to continue",
)
BODY_SNIPPET_LIMIT = 240
MODERN_DESKTOP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/137.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass(frozen=True, slots=True)
class HTTPProbeProfile:
    name: str
    browser: dict[str, Any] | None = None
    header_overrides: dict[str, str] | None = None


@dataclass(slots=True)
class HTTPRequestConfig:
    proxy_url: str | None = None
    cookie_header: str | None = None
    cookie_jar: RequestsCookieJar | None = None
    trust_env: bool = True

    @property
    def proxy_label(self) -> str:
        if not self.proxy_url:
            return "direct"
        parts = urlsplit(self.proxy_url)
        if not parts.scheme or not parts.hostname:
            return "proxy"
        if parts.port is not None:
            return f"{parts.scheme}://{parts.hostname}:{parts.port}"
        return f"{parts.scheme}://{parts.hostname}"

    @property
    def cookie_mode(self) -> str:
        if self.cookie_header:
            return "header"
        if self.cookie_jar is not None:
            return "json"
        return "none"


@dataclass(slots=True)
class HTTPProbeResult:
    profile: str
    url: str
    final_url: str = ""
    status_code: int | None = None
    blocked: bool = False
    block_marker_found: bool = False
    server: str = ""
    cf_ray: str = ""
    content_type: str = ""
    user_agent: str = ""
    response_bytes: int = 0
    redirect_count: int = 0
    proxy_label: str = ""
    cookie_mode: str = "none"
    error_kind: str = ""
    error_detail: str = ""
    body_snippet: str = ""

    def to_payload(self) -> dict[str, object]:
        return asdict(self)


PROBE_PROFILES = (
    HTTPProbeProfile(name="default"),
    HTTPProbeProfile(
        name="cloudscraper_windows",
        browser={"browser": "chrome", "platform": "windows", "mobile": False},
    ),
    HTTPProbeProfile(
        name="modern_desktop_ua",
        header_overrides=MODERN_DESKTOP_HEADERS,
    ),
)
DEFAULT_PROBE_PROFILE = PROBE_PROFILES[0]


class HTTPTransport:
    """Cloudscraper-backed HTTP transport for crawl commands."""

    def __init__(
        self,
        settings: Settings,
        *,
        verbose: bool = False,
        profile: HTTPProbeProfile = DEFAULT_PROBE_PROFILE,
        request_config: HTTPRequestConfig | None = None,
    ) -> None:
        self.settings = settings
        self.verbose = verbose
        self.profile = profile
        self.request_config = request_config
        self._client: cloudscraper.CloudScraper | None = None

    def __enter__(self) -> HTTPTransport:
        self._emit_verbose(f"initialize client profile={self.profile.name}")
        self._client = self.build_client()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
        return None

    def build_client(self) -> cloudscraper.CloudScraper:
        self._emit_verbose(f"build cloudscraper client profile={self.profile.name}")
        scraper_kwargs: dict[str, Any] = {}
        if self.profile.browser is not None:
            scraper_kwargs["browser"] = self.profile.browser
        client = cloudscraper.create_scraper(**scraper_kwargs)
        if self.profile.header_overrides:
            client.headers.update(self.profile.header_overrides)
        if self.request_config is not None:
            self._apply_request_config(client)
        return client

    def fetch(self, url: str) -> str:
        client = self._require_client()
        html, result = self._request_with_diagnostics(client, url)
        if result.error_kind == "challenge_page":
            raise CrawlBlockedError(self._blocked_message(result))
        if result.error_kind:
            raise TransportError(result.error_detail)
        assert html is not None
        return html

    def probe(self, url: str) -> HTTPProbeResult:
        client = self._require_client()
        _html, result = self._request_with_diagnostics(client, url)
        return result

    def close(self) -> None:
        if self._client is not None:
            self._emit_verbose("close client")
            self._client.close()
        self._client = None

    def _apply_request_config(self, client: cloudscraper.CloudScraper) -> None:
        client.trust_env = self.request_config.trust_env
        if self.request_config.proxy_url:
            client.proxies.update(_build_proxy_mapping(self.request_config.proxy_url))
        elif not self.request_config.trust_env:
            client.proxies.clear()
        if self.request_config.cookie_header:
            client.headers["Cookie"] = self.request_config.cookie_header
        if self.request_config.cookie_jar is not None:
            client.cookies.update(_clone_cookie_jar(self.request_config.cookie_jar))

    def _request_with_diagnostics(
        self,
        client: cloudscraper.CloudScraper,
        url: str,
    ) -> tuple[str | None, HTTPProbeResult]:
        result = HTTPProbeResult(
            profile=self.profile.name,
            url=url,
            user_agent=self._get_user_agent(client),
            proxy_label=self.request_config.proxy_label if self.request_config else "",
            cookie_mode=self.request_config.cookie_mode if self.request_config else "none",
        )
        self._emit_verbose(
            f"GET {url} profile={result.profile} "
            f"proxy={result.proxy_label or 'runtime-default'} "
            f"cookies={result.cookie_mode}"
        )

        try:
            response = client.get(url, timeout=60, allow_redirects=True)
            html = self._response_to_html(response)
            self._populate_result_from_response(result, response, html)
            response.raise_for_status()
        except HTTPError as exc:
            response = exc.response
            html = self._response_to_html(response) if response is not None else ""
            if response is not None:
                self._populate_result_from_response(result, response, html)
                self._emit_verbose(f"HTTP {response.status_code} {url}")
            if html and self._looks_blocked(html):
                result.blocked = True
                result.block_marker_found = True
                result.error_kind = "challenge_page"
                result.error_detail = self._blocked_message(result)
                return None, result
            status = result.status_code if result.status_code is not None else "unknown"
            result.error_kind = "http_error"
            result.error_detail = f"HTTP {status} while fetching {url}"
            return None, result
        except RequestException as exc:
            response = getattr(exc, "response", None)
            html = self._response_to_html(response) if response is not None else ""
            if response is not None:
                self._populate_result_from_response(result, response, html)
                self._emit_verbose(f"HTTP {response.status_code} {url}")
            if html and self._looks_blocked(html):
                result.blocked = True
                result.block_marker_found = True
                result.error_kind = "challenge_page"
                result.error_detail = self._blocked_message(result)
                return None, result
            result.error_kind = "network_error"
            result.error_detail = f"Network error while fetching {url}: {exc}"
            return None, result

        self._emit_verbose(
            f"OK {url} status={result.status_code} bytes={result.response_bytes}"
        )
        if self._looks_blocked(html):
            result.blocked = True
            result.block_marker_found = True
            result.error_kind = "challenge_page"
            result.error_detail = self._blocked_message(result)
            return html, result
        return html, result

    def _populate_result_from_response(
        self,
        result: HTTPProbeResult,
        response,
        html: str,
    ) -> None:
        result.final_url = str(getattr(response, "url", "") or "")
        result.status_code = getattr(response, "status_code", None)
        headers = getattr(response, "headers", {}) or {}
        result.server = str(headers.get("server", "") or "")
        result.cf_ray = str(headers.get("cf-ray", "") or "")
        result.content_type = str(headers.get("content-type", "") or "")
        result.response_bytes = len(html.encode("utf-8"))
        result.redirect_count = len(getattr(response, "history", []) or [])
        result.body_snippet = self._summarize_body(html)

    def _require_client(self) -> cloudscraper.CloudScraper:
        if self._client is None:
            raise TransportError(
                "The HTTP transport has not been initialized. "
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

    def _summarize_body(self, html: str) -> str:
        collapsed = " ".join(html.split())
        return collapsed[:BODY_SNIPPET_LIMIT]

    def _blocked_message(self, result: HTTPProbeResult) -> str:
        details = [
            f"profile={result.profile}",
            f"status={result.status_code if result.status_code is not None else 'unknown'}",
        ]
        if result.proxy_label:
            details.append(f"proxy={result.proxy_label}")
        if result.cookie_mode != "none":
            details.append(f"cookies={result.cookie_mode}")
        if result.server:
            details.append(f"server={result.server}")
        if result.cf_ray:
            details.append(f"cf-ray={result.cf_ray}")
        details.append(
            f"challenge_marker={'yes' if result.block_marker_found else 'no'}"
        )
        return "The HTTP crawler received a blocked or challenge page " f"({', '.join(details)})."

    def _get_user_agent(self, client: cloudscraper.CloudScraper) -> str:
        headers = getattr(client, "headers", {}) or {}
        return str(headers.get("User-Agent", "") or "")

    def _emit_verbose(self, message: str) -> None:
        if not self.verbose:
            return
        print(f"[http] {message}", file=sys.stderr, flush=True)


def list_probe_profile_names() -> tuple[str, ...]:
    return tuple(profile.name for profile in PROBE_PROFILES)


def load_probe_cookie_jar(path: str | Path) -> RequestsCookieJar:
    cookie_path = Path(path)
    try:
        payload = json.loads(cookie_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TransportError(f"Could not read cookie JSON from {cookie_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise TransportError(
            f"Cookie JSON at {cookie_path} is not valid JSON: {exc}"
        ) from exc

    entries = payload.get("cookies") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise TransportError(
            "Cookie JSON must be an array of cookie objects or an object with a 'cookies' array."
        )

    jar = RequestsCookieJar()
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise TransportError(f"Cookie entry #{index + 1} is not an object.")
        name = item.get("name")
        value = item.get("value")
        if not isinstance(name, str) or not name:
            raise TransportError(f"Cookie entry #{index + 1} is missing a valid 'name'.")
        if not isinstance(value, str):
            raise TransportError(f"Cookie entry #{index + 1} is missing a valid 'value'.")
        cookie_kwargs: dict[str, Any] = {"name": name, "value": value}
        domain = item.get("domain")
        if isinstance(domain, str) and domain:
            cookie_kwargs["domain"] = domain
        path_value = item.get("path")
        if isinstance(path_value, str) and path_value:
            cookie_kwargs["path"] = path_value
        secure = item.get("secure")
        if isinstance(secure, bool):
            cookie_kwargs["secure"] = secure
        expires = item.get("expires")
        if isinstance(expires, int):
            cookie_kwargs["expires"] = expires
        jar.set_cookie(create_cookie(**cookie_kwargs))
    return jar


def run_http_probe_matrix(
    settings: Settings,
    url: str,
    *,
    verbose: bool = False,
    proxy_url: str | None = None,
    cookie_header: str | None = None,
    cookie_jar: RequestsCookieJar | None = None,
    profile_name: str | None = None,
) -> list[HTTPProbeResult]:
    if cookie_header and cookie_jar is not None:
        raise TransportError("Choose either a raw cookie header or a cookie JSON file, not both.")

    profiles = _select_probe_profiles(profile_name)
    request_configs = _build_probe_request_configs(
        proxy_url=proxy_url,
        cookie_header=cookie_header,
        cookie_jar=cookie_jar,
    )
    results: list[HTTPProbeResult] = []
    for request_config in request_configs:
        for profile in profiles:
            try:
                with HTTPTransport(
                    settings,
                    verbose=verbose,
                    profile=profile,
                    request_config=request_config,
                ) as transport:
                    results.append(transport.probe(url))
            except Exception as exc:
                results.append(
                    HTTPProbeResult(
                        profile=profile.name,
                        url=url,
                        user_agent=profile.header_overrides.get("User-Agent", "")
                        if profile.header_overrides
                        else "",
                        proxy_label=request_config.proxy_label,
                        cookie_mode=request_config.cookie_mode,
                        error_kind="client_error",
                        error_detail=str(exc),
                    )
                )
    return results


def _build_proxy_mapping(proxy_url: str) -> dict[str, str]:
    return {"http": proxy_url, "https": proxy_url}


def _clone_cookie_jar(cookie_jar: RequestsCookieJar) -> RequestsCookieJar:
    clone = RequestsCookieJar()
    for cookie in cookie_jar:
        clone.set_cookie(
            create_cookie(
                name=cookie.name,
                value=cookie.value,
                domain=cookie.domain,
                path=cookie.path,
                secure=cookie.secure,
                expires=cookie.expires,
            )
        )
    return clone


def _select_probe_profiles(profile_name: str | None) -> tuple[HTTPProbeProfile, ...]:
    if profile_name is None:
        return PROBE_PROFILES
    for profile in PROBE_PROFILES:
        if profile.name == profile_name:
            return (profile,)
    available = ", ".join(list_probe_profile_names())
    raise TransportError(f"Unknown probe profile '{profile_name}'. Available: {available}")


def _build_probe_request_configs(
    *,
    proxy_url: str | None,
    cookie_header: str | None,
    cookie_jar: RequestsCookieJar | None,
) -> list[HTTPRequestConfig]:
    request_configs = [HTTPRequestConfig(trust_env=False)]
    cookie_config: HTTPRequestConfig | None = None
    if cookie_header:
        cookie_config = HTTPRequestConfig(cookie_header=cookie_header, trust_env=False)
    elif cookie_jar is not None:
        cookie_config = HTTPRequestConfig(
            cookie_jar=_clone_cookie_jar(cookie_jar),
            trust_env=False,
        )
    if cookie_config is not None:
        request_configs.append(cookie_config)
    if proxy_url:
        request_configs.append(HTTPRequestConfig(proxy_url=proxy_url, trust_env=False))
        if cookie_header:
            request_configs.append(
                HTTPRequestConfig(
                    proxy_url=proxy_url,
                    cookie_header=cookie_header,
                    trust_env=False,
                )
            )
        elif cookie_jar is not None:
            request_configs.append(
                HTTPRequestConfig(
                    proxy_url=proxy_url,
                    cookie_jar=_clone_cookie_jar(cookie_jar),
                    trust_env=False,
                )
            )
    return request_configs
