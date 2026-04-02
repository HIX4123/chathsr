from __future__ import annotations

from pathlib import Path

import pytest

from chathsr.config import Settings


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    settings = Settings(
        project_root=tmp_path,
        data_dir=data_dir,
        database_path=data_dir / "test.sqlite3",
        sync_inbox_dir=data_dir / "inbox",
        sync_archive_dir=data_dir / "sync-archive",
        sync_client_outbox_dir=data_dir / "sync-outbox",
        sync_remote_host="sync.example.com",
        sync_remote_user="ubuntu",
        sync_remote_path="/srv/chathsr/inbox",
        sync_ssh_port=22,
        gemini_api_key="test-key",
        generation_model="gemini-3-flash-preview",
        cheap_generation_model="gemini-3.1-flash-lite-preview",
        embedding_model="gemini-embedding-2-preview",
        embedding_dim=1536,
        board_slug="hkstarrail",
        category_label="정보",
        top_k=6,
    )
    settings.ensure_directories()
    return settings
