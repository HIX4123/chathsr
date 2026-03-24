from __future__ import annotations

import json
from pathlib import Path

from chathsr.websocket_probe import (
    ProbeState,
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


def test_summarize_probe_records_classifies_content_like_payload() -> None:
    records = load_probe_records(FIXTURES / "ws_probe_full_content.jsonl")
    summary = summarize_probe_records(records)

    assert summary["classification"] == "transport-candidate"


def test_classify_probe_payloads_without_frames() -> None:
    classification, reason = classify_probe_payloads([])
    assert classification == "no-signal"
    assert "No websocket payload frames" in reason
