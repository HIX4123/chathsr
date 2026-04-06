from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chathsr.db import Database
from chathsr.errors import RemoteSyncError
from chathsr.live_sync import (
    LiveSyncCycleResult,
    RemoteSyncTarget,
    resolve_remote_sync_target,
    run_live_sync,
    run_live_sync_cycle,
)
from tests.helpers import make_article


class _FakeCrawler:
    def __init__(
        self,
        settings,
        *,
        sync_stats: dict[str, int] | None = None,
        on_sync=None,
    ) -> None:
        self.settings = settings
        self.sync_stats = sync_stats or {
            "pages": 0,
            "articles": 0,
            "new_posts": 0,
            "changed_posts": 0,
            "failed_articles": 0,
        }
        self.on_sync = on_sync

    def sync(
        self,
        db,
        *,
        max_pages: int | None = None,
        unchanged_limit: int = 20,
        verbose: bool = False,
    ) -> dict[str, int]:
        if self.on_sync is not None:
            self.on_sync(db=db, max_pages=max_pages, unchanged_limit=unchanged_limit, verbose=verbose)
        return dict(self.sync_stats)


def test_resolve_remote_sync_target_accepts_cli_overrides(settings, tmp_path: Path) -> None:
    identity_file = tmp_path / "id_ed25519"
    target = resolve_remote_sync_target(
        settings,
        ssh_host="server.example.com",
        ssh_user="deploy",
        ssh_port=2222,
        ssh_identity_file=identity_file,
        remote_inbox_dir="/srv/chathsr/inbox",
        remote_rag_bin="/srv/chathsr/.venv/bin/rag",
    )

    assert target == RemoteSyncTarget(
        ssh_host="server.example.com",
        ssh_user="deploy",
        ssh_port=2222,
        ssh_identity_file=identity_file,
        remote_inbox_dir="/srv/chathsr/inbox",
        remote_rag_bin="/srv/chathsr/.venv/bin/rag",
    )


def test_run_live_sync_cycle_skips_upload_when_no_posts_are_pending(settings) -> None:
    db = Database(settings.database_path)
    target = RemoteSyncTarget(
        ssh_host="server.example.com",
        ssh_user="deploy",
        ssh_port=22,
        remote_inbox_dir="/srv/chathsr/inbox",
        remote_rag_bin="/srv/chathsr/.venv/bin/rag",
    )

    def fail_runner(*args, **kwargs):
        raise AssertionError("command runner should not be called when nothing is pending")

    try:
        result = run_live_sync_cycle(
            settings,
            db,
            target,
            crawler_factory=lambda current_settings: _FakeCrawler(current_settings),
            command_runner=fail_runner,
        )
    finally:
        db.close()

    assert result == LiveSyncCycleResult(
        sync_stats={
            "pages": 0,
            "articles": 0,
            "new_posts": 0,
            "changed_posts": 0,
            "failed_articles": 0,
        },
        pending_posts=0,
        exported_posts=0,
        marked_posts=0,
        uploaded=False,
        remote_file=None,
    )


def test_run_live_sync_cycle_uploads_and_marks_pending_posts(settings) -> None:
    db = Database(settings.database_path)
    article = make_article(
        post_id=12345678,
        title="반디 픽업 정리",
        body_text="반디는 격파 중심 조합을 사용한다.",
    )
    db.upsert_article(article)
    target = RemoteSyncTarget(
        ssh_host="server.example.com",
        ssh_user="deploy",
        ssh_port=2222,
        remote_inbox_dir="/srv/chathsr/inbox",
        remote_rag_bin="/srv/chathsr/.venv/bin/rag",
        ssh_identity_file=Path("/keys/id_ed25519"),
    )
    commands: list[list[str]] = []

    def fake_runner(command, **kwargs):
        commands.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    try:
        result = run_live_sync_cycle(
            settings,
            db,
            target,
            verbose=True,
            crawler_factory=lambda current_settings: _FakeCrawler(current_settings),
            command_runner=fake_runner,
        )
        row = db.get_post(article.post_id)
    finally:
        db.close()

    assert result.pending_posts == 1
    assert result.exported_posts == 1
    assert result.marked_posts == 1
    assert result.uploaded is True
    assert result.remote_file is not None
    assert result.remote_file.startswith("/srv/chathsr/inbox/live-sync-")
    assert row is not None
    assert row["remote_synced_content_hash"] == article.content_hash
    assert row["remote_synced_at"] is not None
    assert [command[0] for command in commands] == ["ssh", "scp", "ssh"]
    assert commands[0][1:5] == ["-p", "2222", "-i", "/keys/id_ed25519"]
    assert commands[1][1:5] == ["-P", "2222", "-i", "/keys/id_ed25519"]
    assert "mkdir -p /srv/chathsr/inbox" in commands[0][-1]
    assert "import-posts" in commands[2][-1]
    assert "index changed-only" in commands[2][-1]
    assert "rm -f" in commands[2][-1]


def test_run_live_sync_cycle_does_not_mark_posts_when_remote_command_fails(settings) -> None:
    db = Database(settings.database_path)
    article = make_article(
        post_id=998877,
        title="실패 재시도 글",
        body_text="서버 반영이 실패하면 다음 주기에 다시 시도한다.",
    )
    db.upsert_article(article)
    target = RemoteSyncTarget(
        ssh_host="server.example.com",
        ssh_user="deploy",
        ssh_port=22,
        remote_inbox_dir="/srv/chathsr/inbox",
        remote_rag_bin="/srv/chathsr/.venv/bin/rag",
    )
    calls = {"count": 0}

    def fake_runner(command, **kwargs):
        calls["count"] += 1
        if calls["count"] == 3:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr="index changed-only failed",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    try:
        with pytest.raises(RemoteSyncError):
            run_live_sync_cycle(
                settings,
                db,
                target,
                crawler_factory=lambda current_settings: _FakeCrawler(current_settings),
                command_runner=fake_runner,
            )
        row = db.get_post(article.post_id)
    finally:
        db.close()

    assert row is not None
    assert row["remote_synced_content_hash"] is None
    assert row["remote_synced_at"] is None


def test_run_live_sync_continues_after_cycle_error(settings) -> None:
    db = Database(settings.database_path)
    target = RemoteSyncTarget(
        ssh_host="server.example.com",
        ssh_user="deploy",
        ssh_port=22,
        remote_inbox_dir="/srv/chathsr/inbox",
        remote_rag_bin="/srv/chathsr/.venv/bin/rag",
    )
    errors: list[str] = []
    completions: list[LiveSyncCycleResult] = []
    state = {"attempt": 0}

    class _StopLoop(RuntimeError):
        pass

    def fake_cycle(*args, **kwargs):
        state["attempt"] += 1
        if state["attempt"] == 1:
            raise RemoteSyncError("temporary upload failure")
        return LiveSyncCycleResult(
            sync_stats={
                "pages": 1,
                "articles": 2,
                "new_posts": 1,
                "changed_posts": 1,
                "failed_articles": 0,
            },
            pending_posts=1,
            exported_posts=1,
            marked_posts=1,
            uploaded=True,
            remote_file="/srv/chathsr/inbox/live-sync-example.jsonl",
        )

    def on_complete(result: LiveSyncCycleResult) -> None:
        completions.append(result)
        raise _StopLoop()

    def on_error(exc) -> None:
        errors.append(str(exc))

    original = run_live_sync.__globals__["run_live_sync_cycle"]
    run_live_sync.__globals__["run_live_sync_cycle"] = fake_cycle
    try:
        with pytest.raises(_StopLoop):
            run_live_sync(
                settings,
                db,
                target,
                once=False,
                on_cycle_complete=on_complete,
                on_cycle_error=on_error,
                sleeper=lambda _: None,
            )
    finally:
        run_live_sync.__globals__["run_live_sync_cycle"] = original
        db.close()

    assert errors == ["temporary upload failure"]
    assert len(completions) == 1
