from __future__ import annotations

import asyncio
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from chathsr.errors import ProbeError
from chathsr.utils import utc_now_iso


POST_ID_RE = re.compile(r"\b\d{6,10}\b")
HEARTBEAT_RE = re.compile(r"^(?:ping|pong|heartbeat|keepalive|ka|h|\{\s*\})$", re.IGNORECASE)
POST_PATH_RE = re.compile(r"/b/[^/\s|]+/\d{6,10}(?:\?[^|\s]*)?")
CHANNEL_EVENT_RE = re.compile(r"^c\|\d+$")


@dataclass(slots=True)
class ProbeRecord:
    timestamp: str
    page_url: str
    socket_url: str
    direction: str
    opcode: str
    payload_text: str
    payload_size: int

    def to_payload(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "page_url": self.page_url,
            "socket_url": self.socket_url,
            "direction": self.direction,
            "opcode": self.opcode,
            "payload_text": self.payload_text,
            "payload_size": self.payload_size,
        }


@dataclass(slots=True)
class _SocketInfo:
    page_url: str
    socket_url: str


class ProbeState:
    def __init__(self) -> None:
        self.session_page_urls: dict[str, str] = {}
        self.target_sessions: dict[str, str] = {}
        self.socket_infos: dict[tuple[str, str], _SocketInfo] = {}

    def register_attached_target(
        self,
        *,
        session_id: str,
        target_id: str,
        page_url: str,
    ) -> None:
        self.target_sessions[target_id] = session_id
        self.session_page_urls[session_id] = page_url

    def process_message(self, message: dict[str, Any]) -> tuple[list[ProbeRecord], list[str]]:
        records: list[ProbeRecord] = []
        enable_sessions: list[str] = []
        method = message.get("method")
        params = message.get("params", {})
        if not isinstance(params, dict):
            params = {}

        if method == "Target.attachedToTarget":
            session_id = str(params.get("sessionId", ""))
            target_info = params.get("targetInfo", {})
            if isinstance(target_info, dict) and target_info.get("type") == "page" and session_id:
                target_id = str(target_info.get("targetId", ""))
                page_url = str(target_info.get("url", "") or "")
                self.register_attached_target(
                    session_id=session_id,
                    target_id=target_id,
                    page_url=page_url,
                )
                enable_sessions.append(session_id)
            return records, enable_sessions

        if method == "Target.detachedFromTarget":
            session_id = str(params.get("sessionId", ""))
            if session_id:
                self.session_page_urls.pop(session_id, None)
                stale_keys = [key for key in self.socket_infos if key[0] == session_id]
                for key in stale_keys:
                    self.socket_infos.pop(key, None)
            return records, enable_sessions

        if method == "Target.targetInfoChanged":
            target_info = params.get("targetInfo", {})
            if isinstance(target_info, dict):
                target_id = str(target_info.get("targetId", ""))
                session_id = self.target_sessions.get(target_id)
                if session_id:
                    self.session_page_urls[session_id] = str(target_info.get("url", "") or "")
            return records, enable_sessions

        session_id = message.get("sessionId")
        if not isinstance(session_id, str):
            return records, enable_sessions

        if method == "Page.frameNavigated":
            frame = params.get("frame", {})
            if isinstance(frame, dict) and not frame.get("parentId"):
                page_url = str(frame.get("url", "") or "")
                self.session_page_urls[session_id] = page_url
                self._update_page_url_for_session_sockets(session_id, page_url)
            return records, enable_sessions

        if method == "Network.webSocketCreated":
            request_id = str(params.get("requestId", ""))
            socket_url = str(params.get("url", "") or "")
            page_url = self.session_page_urls.get(session_id, "")
            if request_id:
                self.socket_infos[(session_id, request_id)] = _SocketInfo(
                    page_url=page_url,
                    socket_url=socket_url,
                )
            records.append(
                ProbeRecord(
                    timestamp=utc_now_iso(),
                    page_url=page_url,
                    socket_url=socket_url,
                    direction="meta",
                    opcode="created",
                    payload_text="",
                    payload_size=0,
                )
            )
            return records, enable_sessions

        if method in {"Network.webSocketFrameReceived", "Network.webSocketFrameSent"}:
            request_id = str(params.get("requestId", ""))
            response = params.get("response", {})
            if not isinstance(response, dict):
                response = {}
            socket_info = self.socket_infos.get(
                (session_id, request_id),
                _SocketInfo(
                    page_url=self.session_page_urls.get(session_id, ""),
                    socket_url="",
                ),
            )
            payload_text = str(response.get("payloadData", "") or "")
            records.append(
                ProbeRecord(
                    timestamp=utc_now_iso(),
                    page_url=socket_info.page_url,
                    socket_url=socket_info.socket_url,
                    direction="inbound" if method.endswith("Received") else "outbound",
                    opcode=str(response.get("opcode", "")),
                    payload_text=payload_text,
                    payload_size=len(payload_text.encode("utf-8")),
                )
            )
            return records, enable_sessions

        if method == "Network.webSocketClosed":
            request_id = str(params.get("requestId", ""))
            socket_info = self.socket_infos.pop(
                (session_id, request_id),
                _SocketInfo(
                    page_url=self.session_page_urls.get(session_id, ""),
                    socket_url="",
                ),
            )
            records.append(
                ProbeRecord(
                    timestamp=utc_now_iso(),
                    page_url=socket_info.page_url,
                    socket_url=socket_info.socket_url,
                    direction="meta",
                    opcode="closed",
                    payload_text="",
                    payload_size=0,
                )
            )
        return records, enable_sessions

    def _update_page_url_for_session_sockets(self, session_id: str, page_url: str) -> None:
        for key, socket_info in list(self.socket_infos.items()):
            if key[0] != session_id:
                continue
            self.socket_infos[key] = _SocketInfo(page_url=page_url, socket_url=socket_info.socket_url)


async def run_websocket_probe(
    *,
    cdp_url: str,
    duration: int,
    output_path: str | Path,
    verbose: bool = False,
) -> dict[str, int]:
    resolved_endpoint = _resolve_browser_debugger_ws_url(cdp_url)
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if verbose:
        print(f"[probe] connect to CDP websocket: {resolved_endpoint}", file=sys.stderr, flush=True)
        print(f"[probe] capture duration={duration}s output={output}", file=sys.stderr, flush=True)
    state = ProbeState()
    counts = {"records": 0, "connections": 0}
    async with connect(resolved_endpoint, max_size=None) as websocket:
        client = _CDPClient(websocket, state, output)
        await client.call("Target.setDiscoverTargets", {"discover": True})
        await client.call(
            "Target.setAutoAttach",
            {
                "autoAttach": True,
                "waitForDebuggerOnStart": False,
                "flatten": True,
            },
        )
        targets = await client.call("Target.getTargets")
        target_infos = targets.get("targetInfos", [])
        if isinstance(target_infos, list):
            for target_info in target_infos:
                if not isinstance(target_info, dict) or target_info.get("type") != "page":
                    continue
                target_id = target_info.get("targetId")
                if not target_id:
                    continue
                result = await client.call(
                    "Target.attachToTarget",
                    {"targetId": str(target_id), "flatten": True},
                )
                session_id = result.get("sessionId")
                if isinstance(session_id, str) and session_id:
                    state.register_attached_target(
                        session_id=session_id,
                        target_id=str(target_id),
                        page_url=str(target_info.get("url", "") or ""),
                    )
                    await client.call("Network.enable", {}, session_id=session_id)
                    await client.call("Page.enable", {}, session_id=session_id)
        counts = await client.collect_for(duration)
    if verbose:
        print(
            f"[probe] capture complete: records={counts['records']} connections={counts['connections']}",
            file=sys.stderr,
            flush=True,
        )
    return counts


def summarize_probe_file(path: str | Path, *, verbose: bool = False) -> str:
    if verbose:
        print(f"[probe] summarize log: {Path(path).resolve()}", file=sys.stderr, flush=True)
    records = load_probe_records(path)
    summary = summarize_probe_records(records)
    lines = [
        f"records={summary['records']}",
        f"connections={summary['connections']}",
        f"inbound={summary['inbound']}",
        f"outbound={summary['outbound']}",
        f"classification={summary['classification']}",
        f"reason={summary['reason']}",
        "message_types=" + ", ".join(summary["message_types"]) if summary["message_types"] else "message_types=none",
        "post_id_examples=" + ", ".join(summary["post_id_examples"]) if summary["post_id_examples"] else "post_id_examples=none",
        "path_signal_examples=" + ", ".join(summary["path_signal_examples"]) if summary["path_signal_examples"] else "path_signal_examples=none",
    ]
    return "\n".join(lines)


def load_probe_records(path: str | Path) -> list[ProbeRecord]:
    source = Path(path).resolve()
    if not source.exists():
        raise ProbeError(f"Probe log does not exist: {source}")
    records: list[ProbeRecord] = []
    for line_number, raw_line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProbeError(f"{source}: line {line_number} is not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ProbeError(f"{source}: line {line_number} must be a JSON object.")
        records.append(
            ProbeRecord(
                timestamp=str(payload.get("timestamp", "")),
                page_url=str(payload.get("page_url", "")),
                socket_url=str(payload.get("socket_url", "")),
                direction=str(payload.get("direction", "")),
                opcode=str(payload.get("opcode", "")),
                payload_text=str(payload.get("payload_text", "")),
                payload_size=int(payload.get("payload_size", 0)),
            )
        )
    return records


def summarize_probe_records(records: list[ProbeRecord]) -> dict[str, object]:
    connections = {record.socket_url for record in records if record.socket_url}
    payload_records = [record for record in records if record.direction in {"inbound", "outbound"}]
    payloads = [record.payload_text.strip() for record in payload_records if record.payload_text.strip()]
    signatures = Counter(_payload_signature(payload) for payload in payloads)
    classification, reason = classify_probe_payloads(payloads)
    post_ids = sorted({match for payload in payloads for match in POST_ID_RE.findall(payload)})[:5]
    path_signals = sorted(
        {
            path
            for payload in payloads
            for path in extract_post_signal_paths(payload)
        }
    )[:5]
    return {
        "records": len(records),
        "connections": len(connections),
        "inbound": sum(1 for record in records if record.direction == "inbound"),
        "outbound": sum(1 for record in records if record.direction == "outbound"),
        "classification": classification,
        "reason": reason,
        "message_types": [f"{name} x{count}" for name, count in signatures.most_common(5)],
        "post_id_examples": post_ids,
        "path_signal_examples": path_signals,
    }


def classify_probe_payloads(payloads: list[str]) -> tuple[str, str]:
    if not payloads:
        return ("no-signal", "No websocket payload frames were captured.")

    if any(_looks_like_full_content(payload) for payload in payloads):
        return (
            "transport-candidate",
            "At least one payload looks like full post or list content.",
        )

    trigger_like = [
        payload
        for payload in payloads
        if _looks_like_post_id_signal(payload) or _looks_like_post_path_signal(payload)
    ]
    if trigger_like:
        return (
            "sync-trigger-only",
            "Payloads include post path or post-id style signals without clear full content.",
        )

    if all(_looks_like_status_chatter(payload) for payload in payloads):
        return (
            "low-value",
            "Captured websocket traffic looks like heartbeat or status chatter only.",
        )

    return (
        "unknown",
        "Payloads were captured, but they do not clearly indicate backfill-grade content.",
    )


def _looks_like_heartbeat(payload: str) -> bool:
    stripped = payload.strip()
    if not stripped:
        return True
    if HEARTBEAT_RE.fullmatch(stripped):
        return True
    if stripped in {"1", "2", "3"}:
        return True
    return len(stripped) <= 8 and stripped.isalpha()


def _looks_like_status_chatter(payload: str) -> bool:
    stripped = payload.strip()
    return _looks_like_heartbeat(stripped) or bool(CHANNEL_EVENT_RE.fullmatch(stripped))


def _looks_like_post_id_signal(payload: str) -> bool:
    stripped = payload.strip()
    if not stripped:
        return False
    ids = POST_ID_RE.findall(stripped)
    if not ids:
        return False
    non_id = POST_ID_RE.sub("", stripped).strip()
    return len(non_id) <= 20


def extract_post_signal_paths(payload: str) -> list[str]:
    return POST_PATH_RE.findall(payload.strip())


def _looks_like_post_path_signal(payload: str) -> bool:
    return bool(extract_post_signal_paths(payload))


def _looks_like_full_content(payload: str) -> bool:
    stripped = payload.strip()
    if not stripped:
        return False
    if any(token in stripped for token in ('"title"', '"body"', '"content"', "제목", "본문", "내용")):
        return True
    if len(stripped) >= 120 and (" " in stripped or "\n" in stripped):
        return True
    return False


def _payload_signature(payload: str) -> str:
    stripped = payload.strip()
    if not stripped:
        return "empty"
    if _looks_like_heartbeat(stripped):
        return "heartbeat"
    if CHANNEL_EVENT_RE.fullmatch(stripped):
        return "channel-chatter"
    if _looks_like_post_path_signal(stripped):
        return "post-path-signal"
    if _looks_like_post_id_signal(stripped):
        return "post-id-signal"
    if _looks_like_full_content(stripped):
        return "content-like"
    if stripped.startswith("{") or stripped.startswith("["):
        return "json-other"
    return "text-other"


def _resolve_browser_debugger_ws_url(cdp_url: str) -> str:
    parsed = urlparse(cdp_url)
    if parsed.scheme in {"ws", "wss"}:
        return cdp_url
    if parsed.scheme not in {"http", "https"}:
        raise ProbeError(
            "CDP URL must be an http(s) debugger URL or a ws(s) websocket endpoint."
        )
    version_url = urljoin(cdp_url.rstrip("/") + "/", "json/version")
    request = Request(version_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except OSError as exc:
        raise ProbeError(f"Could not connect to CDP endpoint: {version_url}") from exc
    websocket_url = payload.get("webSocketDebuggerUrl")
    if not websocket_url:
        raise ProbeError(f"CDP endpoint did not return webSocketDebuggerUrl: {version_url}")
    return str(websocket_url)


class _CDPClient:
    def __init__(self, websocket, state: ProbeState, output_path: Path) -> None:
        self.websocket = websocket
        self.state = state
        self.output_path = output_path
        self._next_id = 1
        self._record_count = 0
        self._connection_count = 0
        self._pending_responses: dict[int, dict[str, Any]] = {}

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        message_id = self._next_id
        self._next_id += 1
        payload: dict[str, Any] = {"id": message_id, "method": method}
        if params:
            payload["params"] = params
        if session_id:
            payload["sessionId"] = session_id
        await self.websocket.send(json.dumps(payload))
        deadline = monotonic() + 15.0
        while True:
            if message_id in self._pending_responses:
                response = self._pending_responses.pop(message_id)
            else:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise ProbeError(f"Timed out while waiting for CDP response to {method}.")
                try:
                    response = await asyncio.wait_for(self._recv_json(), timeout=remaining)
                except asyncio.TimeoutError as exc:
                    raise ProbeError(
                        f"Timed out while waiting for CDP response to {method}."
                    ) from exc
            if response.get("id") == message_id:
                if "error" in response:
                    raise ProbeError(f"CDP command {method} failed: {response['error']}")
                result = response.get("result", {})
                return result if isinstance(result, dict) else {}
            other_id = response.get("id")
            if isinstance(other_id, int):
                self._pending_responses[other_id] = response
                continue
            await self._process_unsolicited(response)

    async def collect_for(self, duration: int) -> dict[str, int]:
        end_time = monotonic() + duration
        while monotonic() < end_time:
            timeout = min(1.0, max(0.0, end_time - monotonic()))
            try:
                response = await asyncio.wait_for(self._recv_json(), timeout=timeout)
            except asyncio.TimeoutError:
                continue
            await self._process_unsolicited(response)
        return {"records": self._record_count, "connections": self._connection_count}

    async def _recv_json(self) -> dict[str, Any]:
        try:
            raw = await self.websocket.recv()
        except WebSocketException as exc:
            raise ProbeError(f"CDP websocket error: {exc}") from exc
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProbeError("Received non-JSON message from CDP websocket.") from exc
        if not isinstance(decoded, dict):
            raise ProbeError("Received unexpected CDP message type.")
        return decoded

    async def _process_unsolicited(self, message: dict[str, Any]) -> None:
        records, enable_sessions = self.state.process_message(message)
        if records:
            self._write_records(records)
        for session_id in enable_sessions:
            await self.call("Network.enable", {}, session_id=session_id)
            await self.call("Page.enable", {}, session_id=session_id)

    def _write_records(self, records: list[ProbeRecord]) -> None:
        with self.output_path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record.to_payload(), ensure_ascii=False))
                handle.write("\n")
                self._record_count += 1
                if record.opcode == "created":
                    self._connection_count += 1
