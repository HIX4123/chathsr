from __future__ import annotations

import sqlite3
from pathlib import Path

from chathsr.db import Database
from tests.helpers import make_article


def test_database_migrates_posts_table_for_video_urls(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE posts (
                post_id INTEGER PRIMARY KEY,
                url TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                category_label TEXT,
                created_at TEXT,
                author TEXT,
                body_text TEXT,
                image_urls_json TEXT NOT NULL DEFAULT '[]',
                raw_html TEXT,
                content_hash TEXT NOT NULL,
                indexed_content_hash TEXT,
                indexed_at TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                crawled_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

    db = Database(db_path)
    try:
        columns = {
            row["name"]
            for row in db.conn.execute("PRAGMA table_info(posts)").fetchall()
        }
    finally:
        db.close()

    assert "video_urls_json" in columns
    assert "remote_synced_content_hash" in columns
    assert "remote_synced_at" in columns


def test_select_posts_pending_remote_sync_returns_only_changed_rows(settings) -> None:
    db = Database(settings.database_path)
    try:
        article = make_article(
            post_id=101,
            title="반디 메모",
            body_text="반디는 격파 중심 조합을 사용한다.",
        )
        synced_article = make_article(
            post_id=202,
            title="경류 메모",
            body_text="경류는 치명 세팅을 사용한다.",
        )
        db.upsert_article(article)
        db.upsert_article(synced_article)
        db.mark_posts_remote_synced(
            [(synced_article.post_id, synced_article.content_hash)]
        )

        pending = db.select_posts_pending_remote_sync()
    finally:
        db.close()

    assert [row["post_id"] for row in pending] == [101]


def test_mark_posts_remote_synced_requires_matching_content_hash(settings) -> None:
    db = Database(settings.database_path)
    try:
        original = make_article(
            post_id=303,
            title="원본 글",
            body_text="원본 본문",
        )
        changed = make_article(
            post_id=303,
            title="원본 글",
            body_text="수정된 본문",
        )
        db.upsert_article(original)
        db.upsert_article(changed)

        updated = db.mark_posts_remote_synced(
            [(original.post_id, original.content_hash)]
        )
        row = db.get_post(original.post_id)
    finally:
        db.close()

    assert updated == 0
    assert row is not None
    assert row["remote_synced_content_hash"] is None
    assert row["remote_synced_at"] is None
