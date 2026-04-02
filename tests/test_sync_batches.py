from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from chathsr.cli import _sync_inbox_batches
from chathsr.db import Database
from chathsr.errors import SyncBatchError, SyncConfigurationError
from chathsr.sync_batches import (
    create_sync_batch,
    load_sync_batch_metadata,
    push_sync_batch,
    read_remote_sync_status,
)
from tests.helpers import make_article


def test_create_sync_batch_creates_jsonl_and_metadata(settings, tmp_path: Path) -> None:
    result = create_sync_batch(
        tmp_path,
        [
            make_article(post_id=1, title="반디 메모", body_text="반디 메모 본문"),
            make_article(post_id=2, title="경류 메모", body_text="경류 메모 본문"),
        ],
        settings=settings,
        since_post_id=10,
        recheck_posts=20,
        max_pages=2,
    )

    assert result.batch.jsonl_path.exists()
    assert result.batch.metadata_path.exists()
    payload = json.loads(result.batch.metadata_path.read_text(encoding="utf-8"))
    assert payload["batch_id"] == result.batch.batch_id
    assert payload["article_count"] == 2
    assert payload["since_post_id"] == 10
    assert payload["min_post_id"] == 1
    assert payload["max_post_id"] == 2
    assert payload["recheck_posts"] == 20
    assert payload["max_pages"] == 2
    assert load_sync_batch_metadata(result.batch).article_count == 2


def test_load_sync_batch_metadata_rejects_article_count_mismatch(settings, tmp_path: Path) -> None:
    result = create_sync_batch(
        tmp_path,
        [make_article(post_id=1, title="반디 메모", body_text="반디 메모 본문")],
        settings=settings,
        since_post_id=None,
        recheck_posts=0,
        max_pages=1,
    )
    payload = json.loads(result.batch.metadata_path.read_text(encoding="utf-8"))
    payload["article_count"] = 99
    result.batch.metadata_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SyncBatchError):
        load_sync_batch_metadata(result.batch)


def test_push_sync_batch_uses_ssh_and_scp_commands(settings, tmp_path: Path, monkeypatch) -> None:
    result = create_sync_batch(
        tmp_path,
        [make_article(post_id=1, title="반디 메모", body_text="반디 메모 본문")],
        settings=settings,
        since_post_id=None,
        recheck_posts=0,
        max_pages=1,
    )
    commands: list[list[str]] = []

    monkeypatch.setattr("chathsr.sync_batches.shutil.which", lambda _name: "/usr/bin/fake")
    monkeypatch.setattr(
        "chathsr.sync_batches.subprocess.run",
        lambda command, check, capture_output, text: commands.append(command)
        or subprocess.CompletedProcess(command, 0, "", ""),
    )

    push_sync_batch(result.batch, settings, verbose=False)

    assert [command[0] for command in commands] == ["ssh", "scp", "scp", "ssh"]
    assert commands[0][3] == f"{settings.sync_remote_user}@{settings.sync_remote_host}"
    assert settings.sync_remote_path in commands[0][4]


def test_push_sync_batch_requires_remote_settings(settings, tmp_path: Path) -> None:
    result = create_sync_batch(
        tmp_path,
        [make_article(post_id=1, title="반디 메모", body_text="반디 메모 본문")],
        settings=settings,
        since_post_id=None,
        recheck_posts=0,
        max_pages=1,
    )
    settings.sync_remote_host = None

    with pytest.raises(SyncConfigurationError):
        push_sync_batch(result.batch, settings, verbose=False)


def test_sync_inbox_batches_imports_indexes_and_archives(settings, monkeypatch) -> None:
    result = create_sync_batch(
        settings.sync_inbox_dir,
        [make_article(post_id=1, title="반디 메모", body_text="반디 메모 본문")],
        settings=settings,
        since_post_id=None,
        recheck_posts=0,
        max_pages=1,
    )

    class FakeGeminiClient:
        def __init__(self, _settings) -> None:
            pass

    monkeypatch.setattr("chathsr.cli.GeminiClient", FakeGeminiClient)
    monkeypatch.setattr(
        "chathsr.cli.index_posts",
        lambda _db, _settings, _gemini, *, changed_only, full_reembed, verbose=False: 1,
    )

    db = Database(settings.database_path)
    try:
        stats = _sync_inbox_batches(settings, db, verbose=False)
        assert db.get_sync_batch_status(result.batch.batch_id) == "succeeded"
        assert db.get_post(1) is not None
    finally:
        db.close()

    assert stats["processed_batches"] == 1
    assert stats["failed_batches"] == 0
    assert stats["articles"] == 1
    assert stats["indexed"] == 1
    processed_dir = settings.sync_archive_dir / "processed"
    assert (processed_dir / result.batch.jsonl_path.name).exists()
    assert (processed_dir / result.batch.metadata_path.name).exists()


def test_sync_inbox_batches_marks_failed_batches(settings) -> None:
    result = create_sync_batch(
        settings.sync_inbox_dir,
        [make_article(post_id=1, title="반디 메모", body_text="반디 메모 본문")],
        settings=settings,
        since_post_id=None,
        recheck_posts=0,
        max_pages=1,
    )
    payload = json.loads(result.batch.metadata_path.read_text(encoding="utf-8"))
    payload["article_count"] = 2
    result.batch.metadata_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    db = Database(settings.database_path)
    try:
        stats = _sync_inbox_batches(settings, db, verbose=False)
        assert db.get_sync_batch_status(result.batch.batch_id) == "failed"
    finally:
        db.close()

    assert stats["processed_batches"] == 0
    assert stats["failed_batches"] == 1
    failed_dir = settings.sync_archive_dir / "failed"
    assert (failed_dir / result.batch.jsonl_path.name).exists()
    assert (failed_dir / result.batch.metadata_path.name).exists()


def test_sync_inbox_batches_skips_already_succeeded_batches(settings) -> None:
    result = create_sync_batch(
        settings.sync_inbox_dir,
        [make_article(post_id=1, title="반디 메모", body_text="반디 메모 본문")],
        settings=settings,
        since_post_id=None,
        recheck_posts=0,
        max_pages=1,
    )

    db = Database(settings.database_path)
    try:
        db.record_sync_batch(
            batch_id=result.batch.batch_id,
            source_name=result.batch.jsonl_path.name,
            status="succeeded",
            article_count=1,
        )
        stats = _sync_inbox_batches(settings, db, verbose=False)
    finally:
        db.close()

    assert stats["processed_batches"] == 0
    assert stats["skipped_batches"] == 1
    processed_dir = settings.sync_archive_dir / "processed"
    assert (processed_dir / result.batch.jsonl_path.name).exists()


def test_read_remote_sync_status_uses_ssh(settings, monkeypatch) -> None:
    commands: list[list[str]] = []

    monkeypatch.setattr("chathsr.sync_batches.shutil.which", lambda _name: "/usr/bin/fake")
    monkeypatch.setattr(
        "chathsr.sync_batches.subprocess.run",
        lambda command, check, capture_output, text: commands.append(command)
        or subprocess.CompletedProcess(
            command,
            0,
            (
                '{"latest_post_id": 12345678, "latest_crawled_at": "2026-04-02T00:00:00Z", '
                '"latest_batch_id": "batch-1", '
                '"recent_posts": [{"post_id": 12345678, "content_hash": "hash-1"}]}\n'
            ),
            "",
        ),
    )

    status = read_remote_sync_status(settings, recent_posts=5, verbose=False)

    assert status.latest_post_id == 12345678
    assert status.latest_batch_id == "batch-1"
    assert [(post.post_id, post.content_hash) for post in status.recent_posts] == [
        (12345678, "hash-1")
    ]
    assert commands[0][0] == "ssh"
    assert commands[0][3] == f"{settings.sync_remote_user}@{settings.sync_remote_host}"
    assert "--recent-posts 5" in commands[0][4]
