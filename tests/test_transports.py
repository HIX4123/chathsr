from __future__ import annotations

import builtins
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from chathsr.custom_transport import CustomHTTPTransport
from chathsr.errors import TransportError, UnsupportedTransportError
from chathsr.transports import BrowserTransport, create_transport


def test_browser_transport_uses_cdp_when_configured(settings, monkeypatch) -> None:
    settings.playwright_cdp_url = "http://127.0.0.1:9222"
    calls: list[tuple] = []
    chromium = _DummyChromium(calls)

    monkeypatch.setattr(
        "chathsr.transports._load_playwright_sync_api",
        lambda: (_DummyTimeoutError, lambda: _DummyPlaywrightManager(chromium, calls)),
    )

    with BrowserTransport(settings, headless=True) as transport:
        assert transport is not None

    assert calls[0] == ("connect_over_cdp", "http://127.0.0.1:9222")
    assert calls[1] == ("new_page",)


def test_browser_transport_uses_storage_state_when_available(settings, monkeypatch) -> None:
    settings.playwright_storage_state_path.write_text(
        '{"cookies": [], "origins": []}',
        encoding="utf-8",
    )
    calls: list[tuple] = []
    chromium = _DummyChromium(calls)

    monkeypatch.setattr(
        "chathsr.transports._load_playwright_sync_api",
        lambda: (_DummyTimeoutError, lambda: _DummyPlaywrightManager(chromium, calls)),
    )

    with BrowserTransport(settings, headless=True) as transport:
        assert transport is not None

    assert calls[0] == ("launch", True)
    assert calls[1] == ("new_context", str(settings.playwright_storage_state_path))


def test_browser_transport_falls_back_to_persistent_profile(settings, monkeypatch) -> None:
    calls: list[tuple] = []
    chromium = _DummyChromium(calls)

    monkeypatch.setattr(
        "chathsr.transports._load_playwright_sync_api",
        lambda: (_DummyTimeoutError, lambda: _DummyPlaywrightManager(chromium, calls)),
    )

    with BrowserTransport(settings, headless=True) as transport:
        assert transport is not None

    assert calls[0] == (
        "launch_persistent_context",
        str(settings.playwright_profile_dir),
        True,
    )


def test_create_transport_returns_custom_http_placeholder(settings) -> None:
    transport = create_transport(settings, "custom-http")
    try:
        assert isinstance(transport, CustomHTTPTransport)
    finally:
        transport.close()


def test_create_transport_returns_browser_transport(settings, monkeypatch) -> None:
    calls: list[tuple] = []
    chromium = _DummyChromium(calls)

    monkeypatch.setattr(
        "chathsr.transports._load_playwright_sync_api",
        lambda: (_DummyTimeoutError, lambda: _DummyPlaywrightManager(chromium, calls)),
    )

    with create_transport(settings, "browser") as transport:
        assert isinstance(transport, BrowserTransport)


def test_create_transport_rejects_unknown_name(settings) -> None:
    with pytest.raises(UnsupportedTransportError):
        create_transport(settings, "nope")


def test_custom_http_transport_builds_cloudscraper_client(settings) -> None:
    with CustomHTTPTransport(settings) as transport:
        assert transport._client is not None


def test_custom_http_transport_fetch_requires_context(settings) -> None:
    transport = CustomHTTPTransport(settings)

    with pytest.raises(TransportError, match="context manager"):
        transport.fetch("https://arca.live/")


def test_custom_http_transport_verbose_logs_requested_url(settings, monkeypatch, capsys) -> None:
    monkeypatch.setattr(CustomHTTPTransport, "build_client", lambda self: _FakeHTTPClient())

    with CustomHTTPTransport(settings, verbose=True) as transport:
        html = transport.fetch("https://arca.live/b/hkstarrail")

    captured = capsys.readouterr()
    assert html == "<html>ok</html>"
    assert "[custom-http] GET https://arca.live/b/hkstarrail" in captured.err
    assert "status=200" in captured.err


def test_transports_module_import_is_lazy_for_playwright(monkeypatch) -> None:
    sys.modules.pop("chathsr.transports", None)
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "playwright.sync_api":
            raise AssertionError("playwright.sync_api should not load during module import")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    module = importlib.import_module("chathsr.transports")
    assert module.DEFAULT_TRANSPORT == "browser"
    sys.modules.pop("chathsr.transports", None)


class _DummyTimeoutError(Exception):
    pass


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code
        self.encoding = "utf-8"

    def raise_for_status(self) -> None:
        return None


class _FakeCookies:
    pass


class _FakeHTTPClient:
    def get(self, url: str, timeout: int, allow_redirects: bool):
        return _FakeResponse("<html>ok</html>", 200)

    def close(self) -> None:
        return None


class _DummyPage:
    def __init__(self) -> None:
        self.timeout = None
        self.closed = False

    def set_default_timeout(self, timeout: int) -> None:
        self.timeout = timeout

    def close(self) -> None:
        self.closed = True


class _DummyContext:
    def __init__(self, calls: list[tuple] | None = None) -> None:
        self.calls = calls
        self.pages = [_DummyPage()]
        self.closed = False

    def new_page(self):
        if self.calls is not None:
            self.calls.append(("new_page",))
        page = _DummyPage()
        self.pages.append(page)
        return page

    def close(self) -> None:
        self.closed = True


class _DummyBrowser:
    def __init__(self, calls: list[tuple], *, contexts: list[_DummyContext] | None = None) -> None:
        self.calls = calls
        self.contexts = contexts or []
        self.closed = False

    def new_context(self, *, storage_state: str):
        self.calls.append(("new_context", storage_state))
        return _DummyContext()

    def close(self) -> None:
        self.closed = True
        self.calls.append(("browser_close",))


class _DummyChromium:
    def __init__(self, calls: list[tuple]) -> None:
        self.calls = calls

    def launch(self, *, headless: bool):
        self.calls.append(("launch", headless))
        return _DummyBrowser(self.calls)

    def launch_persistent_context(self, user_data_dir: str, *, headless: bool):
        self.calls.append(("launch_persistent_context", user_data_dir, headless))
        return _DummyContext()

    def connect_over_cdp(self, endpoint_url: str):
        self.calls.append(("connect_over_cdp", endpoint_url))
        return _DummyBrowser(self.calls, contexts=[_DummyContext(self.calls)])


class _DummyPlaywrightManager:
    def __init__(self, chromium: _DummyChromium, calls: list[tuple]) -> None:
        self.chromium = chromium
        self.calls = calls

    def start(self):
        return SimpleNamespace(chromium=self.chromium)

    def stop(self) -> None:
        self.calls.append(("playwright_stop",))
