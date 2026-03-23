from __future__ import annotations

import pytest

from chathsr.db import Database
from chathsr.errors import EmbeddingSpaceMismatchError
from chathsr.indexing import index_posts
from tests.helpers import FakeGemini, make_article


def test_index_changed_only_skips_unchanged_posts(settings) -> None:
    db = Database(settings.database_path)
    try:
        gemini = FakeGemini()
        article = make_article(
            post_id=1,
            title="반디 픽업 정리",
            body_text="반디는 격파 중심 조합을 사용한다.",
        )
        db.upsert_article(article)

        first_count = index_posts(
            db,
            settings,
            gemini,
            changed_only=True,
            full_reembed=False,
        )
        second_count = index_posts(
            db,
            settings,
            gemini,
            changed_only=True,
            full_reembed=False,
        )

        assert first_count == 1
        assert second_count == 0
        assert db.count_chunks() == 1
    finally:
        db.close()


def test_index_changed_only_reindexes_modified_posts(settings) -> None:
    db = Database(settings.database_path)
    try:
        gemini = FakeGemini()
        original = make_article(
            post_id=1,
            title="반디 픽업 정리",
            body_text="반디는 격파 중심 조합을 사용한다.",
        )
        updated = make_article(
            post_id=1,
            title="반디 픽업 정리",
            body_text="반디는 격파 중심 조합을 사용하고 속도 세팅이 중요하다.",
        )
        db.upsert_article(original)
        index_posts(db, settings, gemini, changed_only=True, full_reembed=False)
        db.upsert_article(updated)

        count = index_posts(
            db,
            settings,
            gemini,
            changed_only=True,
            full_reembed=False,
        )

        row = db.get_post(1)
        assert count == 1
        assert row["indexed_content_hash"] == row["content_hash"]
    finally:
        db.close()


def test_embedding_model_change_requires_full_reembed(settings) -> None:
    db = Database(settings.database_path)
    try:
        gemini = FakeGemini()
        article = make_article(
            post_id=1,
            title="반디 픽업 정리",
            body_text="반디는 격파 중심 조합을 사용한다.",
        )
        db.upsert_article(article)
        index_posts(db, settings, gemini, changed_only=True, full_reembed=False)

        migrated_settings = settings
        migrated_settings.embedding_model = "gemini-embedding-001"

        with pytest.raises(EmbeddingSpaceMismatchError):
            index_posts(
                db,
                migrated_settings,
                gemini,
                changed_only=True,
                full_reembed=False,
            )
    finally:
        db.close()
