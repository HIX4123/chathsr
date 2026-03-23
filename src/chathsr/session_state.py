from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from chathsr.errors import StorageStateError


ARCALIVE_DOMAINS = {"arca.live", ".arca.live"}


def validate_storage_state_file(path: str | Path) -> dict[str, Any]:
    file_path = Path(path).resolve()
    if not file_path.exists():
        raise StorageStateError(f"Storage state file does not exist: {file_path}")

    payload = load_json_file(file_path)
    return validate_storage_state_payload(payload)


def load_json_file(path: str | Path) -> Any:
    file_path = Path(path).resolve()
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StorageStateError(
            f"Session file is not valid JSON: {file_path}"
        ) from exc
    except OSError as exc:
        raise StorageStateError(
            f"Could not read session file: {file_path}"
        ) from exc


def validate_storage_state_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise StorageStateError("Storage state JSON must be an object.")
    for key in ("cookies", "origins"):
        if key not in payload:
            raise StorageStateError(
                f"Storage state JSON is missing required key: {key}"
            )
        if not isinstance(payload[key], list):
            raise StorageStateError(
                f"Storage state key `{key}` must be a list."
            )
    return payload


def import_storage_state_file(source: str | Path, destination: str | Path) -> tuple[Path, str]:
    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    payload = load_json_file(source_path)
    storage_state, detected_format = detect_and_normalize_session_payload(payload)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination_path.write_text(
        json.dumps(storage_state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return destination_path, detected_format


def detect_and_normalize_session_payload(payload: Any) -> tuple[dict[str, Any], str]:
    if _looks_like_storage_state(payload):
        return validate_storage_state_payload(payload), "storage_state"
    if _looks_like_cookie_export(payload):
        return convert_cookie_payload_to_storage_state(payload), "cookie_json"
    raise StorageStateError(
        "Unsupported session file format. Provide a Playwright storage_state.json "
        "or a browser-extension cookie JSON export."
    )


def convert_cookie_payload_to_storage_state(payload: Any) -> dict[str, Any]:
    cookies_payload = payload["cookies"] if isinstance(payload, dict) else payload
    assert isinstance(cookies_payload, list)
    cookies: list[dict[str, Any]] = []
    for raw_cookie in cookies_payload:
        cookie = normalize_browser_cookie(raw_cookie)
        domain = cookie["domain"]
        if domain not in ARCALIVE_DOMAINS:
            continue
        cookies.append(cookie)
    if not cookies:
        raise StorageStateError(
            "Cookie export does not contain any cookies for arca.live."
        )
    return {"cookies": cookies, "origins": []}


def normalize_browser_cookie(raw_cookie: Any) -> dict[str, Any]:
    if not isinstance(raw_cookie, dict):
        raise StorageStateError("Each cookie entry must be an object.")
    for field in ("name", "value", "domain"):
        if not raw_cookie.get(field):
            raise StorageStateError(
                f"Cookie entry is missing required field: {field}"
            )

    name = str(raw_cookie["name"])
    value = str(raw_cookie["value"])
    domain = str(raw_cookie["domain"])
    path = str(raw_cookie.get("path") or "/")
    secure = bool(raw_cookie.get("secure", False))
    http_only = bool(raw_cookie.get("httpOnly", raw_cookie.get("http_only", False)))
    same_site = normalize_same_site(raw_cookie.get("sameSite"))

    cookie: dict[str, Any] = {
        "name": name,
        "value": value,
        "domain": domain,
        "path": path,
        "secure": secure,
        "httpOnly": http_only,
        "sameSite": same_site,
    }

    expires = normalize_cookie_expires(raw_cookie)
    if expires is not None:
        cookie["expires"] = expires
    return cookie


def normalize_same_site(value: Any) -> str:
    if value is None or value == "":
        return "Lax"
    normalized = str(value).strip().lower()
    mapping = {
        "lax": "Lax",
        "strict": "Strict",
        "none": "None",
        "no_restriction": "None",
        "unspecified": "Lax",
    }
    if normalized not in mapping:
        raise StorageStateError(f"Unsupported cookie sameSite value: {value}")
    return mapping[normalized]


def normalize_cookie_expires(raw_cookie: dict[str, Any]) -> float | None:
    if raw_cookie.get("session") is True:
        return None
    for key in ("expirationDate", "expires"):
        value = raw_cookie.get(key)
        if value in (None, "", -1):
            continue
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise StorageStateError(
                f"Cookie `{raw_cookie.get('name', '<unknown>')}` has invalid `{key}`."
            ) from exc
    return None


def _looks_like_storage_state(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and "cookies" in payload
        and "origins" in payload
    )


def _looks_like_cookie_export(payload: Any) -> bool:
    if isinstance(payload, list):
        return True
    return (
        isinstance(payload, dict)
        and "cookies" in payload
        and "origins" not in payload
    )
