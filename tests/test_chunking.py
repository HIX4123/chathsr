from __future__ import annotations

from chathsr.chunking import chunk_article
from tests.helpers import make_article


def test_chunk_article_adds_header_and_chunks_long_text() -> None:
    paragraph = "반디는 격파 세팅이 중요하다. " * 80
    article = make_article(post_id=1, title="반디 메모", body_text=f"{paragraph}\n\n{paragraph}")
    chunks = chunk_article(article, target_chars=500, overlap_chars=50)
    assert len(chunks) >= 2
    assert chunks[0].chunk_text.startswith("제목: 반디 메모")
    assert any("작성일:" in chunk.chunk_text for chunk in chunks)
