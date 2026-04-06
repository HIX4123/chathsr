from __future__ import annotations

import json
from pathlib import Path

import pytest

from chathsr.db import Database
from chathsr.errors import ImportFormatError
from chathsr.post_exports import export_articles_jsonl, import_articles_jsonl
from tests.helpers import make_article


def test_export_and_import_articles_jsonl_round_trip(settings, tmp_path: Path) -> None:
    export_path = tmp_path / "posts.jsonl"
    article = make_article(
        post_id=12345678,
        title="반디 픽업 정리",
        body_text="반디는 격파 중심 조합을 사용한다.",
        video_urls=["https://video.example/embed/bandi"],
    )

    count = export_articles_jsonl(export_path, [article])

    db = Database(settings.database_path)
    try:
        stats = import_articles_jsonl(export_path, db)
        row = db.get_post(12345678)
    finally:
        db.close()

    assert count == 1
    assert stats == {"files": 1, "articles": 1, "new_posts": 1, "changed_posts": 1}
    assert row is not None
    assert row["title"] == "반디 픽업 정리"
    assert row["body_text"] == "반디는 격파 중심 조합을 사용한다."
    assert row["video_urls_json"] == '["https://video.example/embed/bandi"]'


def test_import_articles_jsonl_accepts_directory(settings, tmp_path: Path) -> None:
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    export_articles_jsonl(
        export_dir / "batch1.jsonl",
        [
            make_article(
                post_id=1,
                title="반디 메모",
                body_text="반디 메모 본문",
            )
        ],
    )
    export_articles_jsonl(
        export_dir / "batch2.jsonl",
        [
            make_article(
                post_id=2,
                title="경류 메모",
                body_text="경류 메모 본문",
            )
        ],
    )

    db = Database(settings.database_path)
    try:
        stats = import_articles_jsonl(export_dir, db)
    finally:
        db.close()

    assert stats == {"files": 2, "articles": 2, "new_posts": 2, "changed_posts": 2}


def test_import_articles_jsonl_rejects_missing_required_fields(settings, tmp_path: Path) -> None:
    broken_path = tmp_path / "broken.jsonl"
    broken_path.write_text(json.dumps({"post_id": 1, "title": "반디"}) + "\n", encoding="utf-8")

    db = Database(settings.database_path)
    try:
        with pytest.raises(ImportFormatError):
            import_articles_jsonl(broken_path, db)
    finally:
        db.close()


def test_import_articles_jsonl_accepts_missing_video_urls(settings, tmp_path: Path) -> None:
    export_path = tmp_path / "legacy.jsonl"
    export_path.write_text(
        json.dumps(
            {
                "post_id": 7,
                "url": "https://arca.live/b/hkstarrail/7",
                "title": "레거시 글",
                "body_text": "본문",
                "image_urls": [],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    db = Database(settings.database_path)
    try:
        stats = import_articles_jsonl(export_path, db)
        row = db.get_post(7)
    finally:
        db.close()

    assert stats == {"files": 1, "articles": 1, "new_posts": 1, "changed_posts": 1}
    assert row is not None
    assert row["video_urls_json"] == "[]"
