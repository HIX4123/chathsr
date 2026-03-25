from __future__ import annotations

import asyncio
import json
from pathlib import Path

from chathsr.websocket_probe import (
    ProbeState,
    _CDPClient,
    classify_probe_payloads,
    load_probe_records,
    summarize_probe_records,
)


FIXTURES = Path(__file__).parent / "fixtures"


def test_probe_state_emits_records_from_cdp_events() -> None:
    events = json.loads((FIXTURES / "ws_cdp_events.json").read_text(encoding="utf-8"))
    state = ProbeState()
    records = []
    sessions_to_enable = []
    for event in events:
        emitted, enable = state.process_message(event)
        records.extend(emitted)
        sessions_to_enable.extend(enable)

    assert sessions_to_enable == ["session-1"]
    assert len(records) == 4
    assert records[0].opcode == "created"
    assert records[0].socket_url == "wss://arca.live/ws"
    assert records[1].direction == "inbound"
    assert records[1].payload_text == "12345678"
    assert records[2].direction == "outbound"
    assert records[3].opcode == "closed"


def test_summarize_probe_records_classifies_heartbeat() -> None:
    records = load_probe_records(FIXTURES / "ws_probe_heartbeat.jsonl")
    summary = summarize_probe_records(records)

    assert summary["classification"] == "low-value"
    assert summary["connections"] == 1


def test_summarize_probe_records_classifies_post_id_signal() -> None:
    records = load_probe_records(FIXTURES / "ws_probe_post_ids.jsonl")
    summary = summarize_probe_records(records)

    assert summary["classification"] == "sync-trigger-only"
    assert "12340000" in summary["post_id_examples"]


def test_summarize_probe_records_classifies_post_path_signal() -> None:
    records = [
        _probe_record("inbound", "c|/b/hkstarrail/165796497?mode=best&category=%EC%A0%95%EB%B3%B4&p=1"),
        _probe_record("inbound", "c|35006"),
    ]
    summary = summarize_probe_records(records)

    assert summary["classification"] == "sync-trigger-only"
    assert "/b/hkstarrail/165796497?mode=best&category=%EC%A0%95%EB%B3%B4&p=1" in summary["path_signal_examples"]


def test_summarize_probe_records_classifies_content_like_payload() -> None:
    records = load_probe_records(FIXTURES / "ws_probe_full_content.jsonl")
    summary = summarize_probe_records(records)

    assert summary["classification"] == "transport-candidate"


def test_classify_probe_payloads_without_frames() -> None:
    classification, reason = classify_probe_payloads([])
    assert classification == "no-signal"
    assert "No websocket payload frames" in reason


def test_cdp_client_preserves_out_of_order_responses(tmp_path) -> None:
    websocket = _FakeWebSocket(
        [
            {
                "method": "Target.attachedToTarget",
                "params": {
                    "sessionId": "session-1",
                    "targetInfo": {
                        "targetId": "target-1",
                        "type": "page",
                        "url": "https://arca.live/b/hkstarrail",
                    },
                },
            },
            {"id": 1, "result": {"targetInfos": []}},
            {"id": 2, "result": {}},
            {"id": 3, "result": {}},
        ]
    )

    async def run() -> dict[str, object]:
        client = _CDPClient(websocket, ProbeState(), tmp_path / "ws-probe.jsonl")
        return await client.call("Target.getTargets")

    result = asyncio.run(run())

    assert result == {"targetInfos": []}
    assert [message["method"] for message in websocket.sent] == [
        "Target.getTargets",
        "Network.enable",
        "Page.enable",
    ]


def _probe_record(direction: str, payload_text: str):
    from chathsr.websocket_probe import ProbeRecord

    return ProbeRecord(
        timestamp="2026-03-25T00:00:00Z",
        page_url="https://arca.live/b/hkstarrail",
        socket_url="wss://arca.live/ws",
        direction=direction,
        opcode="1",
        payload_text=payload_text,
        payload_size=len(payload_text.encode("utf-8")),
    )


class _FakeWebSocket:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = [json.dumps(response) for response in responses]
        self.sent: list[dict[str, object]] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    async def recv(self) -> str:
        if not self.responses:
            raise AssertionError("No more websocket responses were queued.")
        return self.responses.pop(0)
