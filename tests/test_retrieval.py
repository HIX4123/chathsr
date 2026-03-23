from __future__ import annotations

from chathsr.db import Database
from chathsr.indexing import index_posts
from chathsr.retrieval import answer_question, retrieve_chunks
from tests.helpers import FakeGemini, make_article


def test_retrieve_chunks_returns_expected_top_hit(settings) -> None:
    db = Database(settings.database_path)
    try:
        gemini = FakeGemini()
        db.upsert_article(
            make_article(
                post_id=1,
                title="반디 픽업 정리",
                body_text="반디는 격파 중심 조합을 사용한다.",
            )
        )
        db.upsert_article(
            make_article(
                post_id=2,
                title="경류 세팅 요약",
                body_text="경류는 치명타와 속도 세팅이 중요하다.",
            )
        )
        index_posts(db, settings, gemini, changed_only=True, full_reembed=False)

        results = retrieve_chunks(
            db,
            settings,
            gemini,
            question="반디 격파 조합 알려줘",
            top_k=2,
        )

        assert results
        assert results[0].post_id == 1
    finally:
        db.close()


def test_answer_question_appends_sources(settings) -> None:
    db = Database(settings.database_path)
    try:
        gemini = FakeGemini(answer_text="반디는 격파 중심 조합이 핵심입니다. [1]")
        db.upsert_article(
            make_article(
                post_id=1,
                title="반디 픽업 정리",
                body_text="반디는 격파 중심 조합을 사용한다.",
            )
        )
        index_posts(db, settings, gemini, changed_only=True, full_reembed=False)

        answer = answer_question(
            db,
            settings,
            gemini,
            question="반디 조합 알려줘",
        )

        assert "출처:" in answer
        assert "[1] 반디 픽업 정리" in answer
    finally:
        db.close()


def test_answer_question_without_context_returns_fallback(settings) -> None:
    db = Database(settings.database_path)
    try:
        gemini = FakeGemini()
        answer = answer_question(
            db,
            settings,
            gemini,
            question="존재하지 않는 질문",
        )

        assert "근거를 찾지 못했습니다" in answer
        assert "출처:" not in answer
    finally:
        db.close()
