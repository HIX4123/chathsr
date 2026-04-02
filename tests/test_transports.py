from __future__ import annotations

import json

import pytest
from requests.cookies import RequestsCookieJar
from requests.exceptions import HTTPError

from chathsr.errors import CrawlBlockedError, TransportError
from chathsr.http_transport import (
    HTTPProbeResult,
    HTTPRequestConfig,
    HTTPTransport,
    load_probe_cookie_jar,
    run_http_probe_matrix,
)
from chathsr.transports import create_transport


def test_create_transport_returns_http_transport(settings) -> None:
    transport = create_transport(settings)
    try:
        assert isinstance(transport, HTTPTransport)
    finally:
        transport.close()


def test_http_transport_builds_cloudscraper_client(settings) -> None:
    with HTTPTransport(settings) as transport:
        assert transport._client is not None


def test_http_transport_fetch_requires_context(settings) -> None:
    transport = HTTPTransport(settings)

    with pytest.raises(TransportError, match="context manager"):
        transport.fetch("https://arca.live/")


def test_http_transport_verbose_logs_requested_url(settings, monkeypatch, capsys) -> None:
    monkeypatch.setattr(HTTPTransport, "build_client", lambda self: _FakeHTTPClient())

    with HTTPTransport(settings, verbose=True) as transport:
        html = transport.fetch("https://arca.live/b/hkstarrail")

    captured = capsys.readouterr()
    assert html == "<html>ok</html>"
    assert "[http] GET https://arca.live/b/hkstarrail" in captured.err
    assert "status=200" in captured.err


def test_http_transport_detects_challenge_page_and_includes_evidence(
    settings, monkeypatch
) -> None:
    monkeypatch.setattr(
        HTTPTransport,
        "build_client",
        lambda self: _FakeHTTPClient(
            text="<html><title>Just a moment...</title></html>",
            status_code=403,
            raise_http_error=True,
            headers={"server": "cloudflare", "cf-ray": "abc123"},
        ),
    )

    with HTTPTransport(settings) as transport:
        with pytest.raises(CrawlBlockedError, match="server=cloudflare"):
            transport.fetch("https://arca.live/b/hkstarrail")


def test_http_transport_probe_returns_diagnostics_for_challenge_page(
    settings, monkeypatch
) -> None:
    monkeypatch.setattr(
        HTTPTransport,
        "build_client",
        lambda self: _FakeHTTPClient(
            text="<html><title>Just a moment...</title></html>",
            status_code=403,
            raise_http_error=True,
            headers={"server": "cloudflare", "cf-ray": "abc123"},
        ),
    )

    with HTTPTransport(settings) as transport:
        result = transport.probe("https://arca.live/b/hkstarrail")

    assert result.profile == "default"
    assert result.status_code == 403
    assert result.blocked is True
    assert result.block_marker_found is True
    assert result.error_kind == "challenge_page"
    assert result.server == "cloudflare"
    assert result.cf_ray == "abc123"
    assert "Just a moment" in result.body_snippet


def test_http_transport_build_client_applies_probe_request_config(
    settings, monkeypatch
) -> None:
    client = _FakeHTTPClient()
    cookie_jar = RequestsCookieJar()
    cookie_jar.set("cf_clearance", "token", domain="arca.live", path="/")

    monkeypatch.setattr(
        "chathsr.http_transport.cloudscraper.create_scraper",
        lambda **kwargs: client,
    )

    transport = HTTPTransport(
        settings,
        request_config=HTTPRequestConfig(
            proxy_url="http://user:pass@proxy.example:8080",
            cookie_header="foo=bar",
            trust_env=False,
        ),
    )
    transport.build_client()

    assert client.trust_env is False
    assert client.proxies == {
        "http": "http://user:pass@proxy.example:8080",
        "https": "http://user:pass@proxy.example:8080",
    }
    assert client.headers["Cookie"] == "foo=bar"

    transport = HTTPTransport(
        settings,
        request_config=HTTPRequestConfig(cookie_jar=cookie_jar, trust_env=False),
    )
    transport.build_client()

    assert client.cookies.get("cf_clearance") == "token"


def test_http_transport_raises_transport_error_for_non_challenge_http_error(
    settings, monkeypatch
) -> None:
    monkeypatch.setattr(
        HTTPTransport,
        "build_client",
        lambda self: _FakeHTTPClient(
            text="<html>nope</html>",
            status_code=500,
            raise_http_error=True,
        ),
    )

    with HTTPTransport(settings) as transport:
        with pytest.raises(TransportError, match="HTTP 500"):
            transport.fetch("https://arca.live/b/hkstarrail")


def test_run_http_probe_matrix_attempts_all_profiles_in_order(settings, monkeypatch) -> None:
    calls: list[dict] = []

    def fake_create_scraper(**kwargs):
        calls.append(kwargs)
        return _FakeHTTPClient()

    monkeypatch.setattr("chathsr.http_transport.cloudscraper.create_scraper", fake_create_scraper)

    results = run_http_probe_matrix(settings, settings.board_url)

    assert [result.profile for result in results] == [
        "default",
        "cloudscraper_windows",
        "modern_desktop_ua",
    ]
    assert calls[0] == {}
    assert calls[1]["browser"]["platform"] == "windows"
    assert "Chrome/137.0.0.0" in results[2].user_agent
    assert all(result.proxy_label == "direct" for result in results)
    assert all(result.cookie_mode == "none" for result in results)


def test_run_http_probe_matrix_expands_proxy_and_cookie_variants(
    settings, monkeypatch
) -> None:
    monkeypatch.setattr(
        "chathsr.http_transport.cloudscraper.create_scraper",
        lambda **kwargs: _FakeHTTPClient(),
    )
    cookie_jar = RequestsCookieJar()
    cookie_jar.set("cf_clearance", "token", domain="arca.live", path="/")

    results = run_http_probe_matrix(
        settings,
        settings.board_url,
        proxy_url="http://user:pass@proxy.example:8080",
        cookie_jar=cookie_jar,
        profile_name="default",
    )

    assert [(result.proxy_label, result.cookie_mode) for result in results] == [
        ("direct", "none"),
        ("direct", "json"),
        ("http://proxy.example:8080", "none"),
        ("http://proxy.example:8080", "json"),
    ]
    assert all(result.profile == "default" for result in results)


def test_run_http_probe_matrix_rejects_multiple_cookie_sources(settings) -> None:
    cookie_jar = RequestsCookieJar()
    cookie_jar.set("cf_clearance", "token")

    with pytest.raises(TransportError, match="either a raw cookie header or a cookie JSON"):
        run_http_probe_matrix(
            settings,
            settings.board_url,
            cookie_header="foo=bar",
            cookie_jar=cookie_jar,
        )


def test_load_probe_cookie_jar_supports_array_and_cookies_object(tmp_path) -> None:
    cookie_path = tmp_path / "cookies.json"
    cookie_path.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "cf_clearance",
                        "value": "token",
                        "domain": "arca.live",
                        "path": "/",
                        "secure": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    jar = load_probe_cookie_jar(cookie_path)

    cookie = next(iter(jar))
    assert cookie.name == "cf_clearance"
    assert cookie.value == "token"
    assert cookie.domain == "arca.live"
    assert cookie.secure is True


def test_load_probe_cookie_jar_rejects_invalid_shape(tmp_path) -> None:
    cookie_path = tmp_path / "cookies.json"
    cookie_path.write_text(json.dumps({"cookies": {"name": "bad"}}), encoding="utf-8")

    with pytest.raises(TransportError, match="Cookie JSON must be an array"):
        load_probe_cookie_jar(cookie_path)


class _FakeResponse:
    def __init__(
        self,
        text: str,
        status_code: int = 200,
        *,
        raise_http_error: bool = False,
        headers: dict[str, str] | None = None,
        url: str = "https://arca.live/b/hkstarrail",
    ) -> None:
        self.text = text
        self.status_code = status_code
        self.encoding = "utf-8"
        self._raise_http_error = raise_http_error
        self.headers = headers or {}
        self.url = url
        self.history: list[object] = []

    def raise_for_status(self) -> None:
        if self._raise_http_error:
            raise HTTPError(response=self)


class _FakeHTTPClient:
    def __init__(
        self,
        text: str = "<html>ok</html>",
        status_code: int = 200,
        *,
        raise_http_error: bool = False,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._response = _FakeResponse(
            text,
            status_code,
            raise_http_error=raise_http_error,
            headers=headers,
        )
        self.headers: dict[str, str] = {}
        self.proxies: dict[str, str] = {}
        self.trust_env = True
        self.cookies = RequestsCookieJar()

    def get(self, url: str, timeout: int, allow_redirects: bool):
        self._response.url = url
        return self._response

    def close(self) -> None:
        return None
