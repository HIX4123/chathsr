from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from chathsr.config import load_settings
from chathsr.crawl import ArcaLiveCrawler
from chathsr.errors import StorageStateError
from chathsr.session_state import (
    convert_cookie_payload_to_storage_state,
    detect_and_normalize_session_payload,
    import_storage_state_file,
    load_json_file,
    validate_storage_state_file,
)


FIXTURES = Path(__file__).parent / "fixtures"


def test_validate_storage_state_file_accepts_valid_json() -> None:
    payload = validate_storage_state_file(FIXTURES / "storage_state.json")
    assert payload["cookies"][0]["name"] == "cf_clearance"
    assert payload["origins"] == []


def test_validate_storage_state_file_rejects_missing_keys(tmp_path: Path) -> None:
    file_path = tmp_path / "broken_state.json"
    file_path.write_text('{"cookies": []}', encoding="utf-8")

    with pytest.raises(StorageStateError):
        validate_storage_state_file(file_path)


def test_import_storage_state_file_copies_to_destination(settings, tmp_path: Path) -> None:
    source = FIXTURES / "storage_state.json"
    destination = tmp_path / "copied_state.json"

    imported, detected_format = import_storage_state_file(source, destination)

    assert imported == destination.resolve()
    assert load_json_file(imported) == load_json_file(source)
    assert detected_format == "storage_state"


def test_load_settings_honors_storage_state_env_override(monkeypatch, tmp_path: Path) -> None:
    custom_state = tmp_path / "external" / "storage_state.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PLAYWRIGHT_STORAGE_STATE_PATH", str(custom_state))
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_PROFILE_DIR", raising=False)

    settings = load_settings(project_root=tmp_path)

    assert settings.playwright_storage_state_path == custom_state.resolve()
    assert settings.playwright_storage_state_path_configured is True
    assert settings.should_use_storage_state is True


def test_crawler_uses_storage_state_context_when_state_exists(settings, monkeypatch) -> None:
    settings.playwright_storage_state_path.write_text(
        (FIXTURES / "storage_state.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    calls: list[tuple] = []
    chromium = _DummyChromium(calls)

    monkeypatch.setattr(
        "chathsr.crawl.sync_playwright",
        lambda: _DummyPlaywrightContext(chromium),
    )

    crawler = ArcaLiveCrawler(settings)
    with crawler._browser_session(headless=True) as page:
        assert page is not None

    assert calls[0] == ("launch", True)
    assert calls[1] == (
        "new_context",
        str(settings.playwright_storage_state_path),
    )


def test_crawler_falls_back_to_persistent_profile_without_state(settings, monkeypatch) -> None:
    calls: list[tuple] = []
    chromium = _DummyChromium(calls)

    monkeypatch.setattr(
        "chathsr.crawl.sync_playwright",
        lambda: _DummyPlaywrightContext(chromium),
    )

    crawler = ArcaLiveCrawler(settings)
    with crawler._browser_session(headless=True) as page:
        assert page is not None

    assert calls[0] == (
        "launch_persistent_context",
        str(settings.playwright_profile_dir),
        True,
    )


def test_detect_and_normalize_cookie_array_payload() -> None:
    payload = load_json_file(FIXTURES / "browser_cookies.json")

    storage_state, detected_format = detect_and_normalize_session_payload(payload)

    assert detected_format == "cookie_json"
    assert storage_state["origins"] == []
    assert len(storage_state["cookies"]) == 2
    assert storage_state["cookies"][0]["domain"] == ".arca.live"
    assert storage_state["cookies"][0]["sameSite"] == "None"


def test_convert_cookie_payload_filters_non_arcalive_cookies() -> None:
    payload = load_json_file(FIXTURES / "browser_cookies_with_noise.json")

    storage_state = convert_cookie_payload_to_storage_state(payload)

    domains = {cookie["domain"] for cookie in storage_state["cookies"]}
    assert domains == {".arca.live"}


def test_convert_cookie_payload_rejects_missing_required_fields() -> None:
    payload = [{"name": "cf_clearance", "value": "token"}]

    with pytest.raises(StorageStateError):
        convert_cookie_payload_to_storage_state(payload)


class _DummyPage:
    def __init__(self) -> None:
        self.timeout = None

    def set_default_timeout(self, timeout: int) -> None:
        self.timeout = timeout


class _DummyContext:
    def __init__(self) -> None:
        self.pages = [_DummyPage()]
        self.closed = False

    def new_page(self):
        page = _DummyPage()
        self.pages.append(page)
        return page

    def close(self) -> None:
        self.closed = True


class _DummyBrowser:
    def __init__(self, calls: list[tuple]) -> None:
        self.calls = calls
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


class _DummyPlaywrightContext:
    def __init__(self, chromium: _DummyChromium) -> None:
        self.chromium = chromium

    def __enter__(self):
        return SimpleNamespace(chromium=self.chromium)

    def __exit__(self, exc_type, exc, tb) -> None:
        return None
