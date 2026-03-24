from __future__ import annotations

from pathlib import Path

import pytest

from chathsr.config import load_settings
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
    monkeypatch.delenv("PLAYWRIGHT_CDP_URL", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_PROFILE_DIR", raising=False)

    settings = load_settings(project_root=tmp_path)

    assert settings.playwright_storage_state_path == custom_state.resolve()
    assert settings.playwright_storage_state_path_configured is True
    assert settings.should_use_storage_state is True


def test_load_settings_honors_cdp_url_env_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PLAYWRIGHT_CDP_URL", "http://127.0.0.1:9222")
    monkeypatch.delenv("PLAYWRIGHT_STORAGE_STATE_PATH", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_PROFILE_DIR", raising=False)

    settings = load_settings(project_root=tmp_path)

    assert settings.playwright_cdp_url == "http://127.0.0.1:9222"
    assert settings.should_use_cdp is True


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
