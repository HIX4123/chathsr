from __future__ import annotations

from chathsr.models import ParsedArticle, RetrievedChunk
from chathsr.utils import stable_content_hash


def make_article(
    *,
    post_id: int,
    title: str,
    body_text: str,
    category_label: str = "정보",
    created_at: str = "2026-03-22T12:00:00+09:00",
    author: str = "tester",
) -> ParsedArticle:
    image_urls: list[str] = []
    return ParsedArticle(
        post_id=post_id,
        url=f"https://arca.live/b/hkstarrail/{post_id}",
        title=title,
        category_label=category_label,
        created_at=created_at,
        author=author,
        body_text=body_text,
        image_urls=image_urls,
        raw_html="<html></html>",
        content_hash=stable_content_hash(
            title=title,
            category_label=category_label,
            created_at=created_at,
            author=author,
            body_text=body_text,
            image_urls=image_urls,
        ),
    )


class FakeGemini:
    def __init__(self, answer_text: str = "테스트 답변입니다. [1]") -> None:
        self.answer_text = answer_text
        self.document_calls: list[tuple[str | None, list[str]]] = []
        self.query_calls: list[str] = []

    def embed_document_chunks(
        self, chunks: list[str], *, title: str | None = None
    ) -> list[list[float]]:
        self.document_calls.append((title, list(chunks)))
        return [_vectorise(text) for text in chunks]

    def embed_query(self, question: str) -> list[float]:
        self.query_calls.append(question)
        return _vectorise(question)

    def generate_answer(
        self,
        *,
        question: str,
        chunks: list[RetrievedChunk],
        use_cheap_model: bool = False,
    ) -> str:
        if not chunks:
            return "관련 근거를 찾지 못했습니다. 현재 수집된 자료만으로는 답할 수 없습니다."
        return self.answer_text


def _vectorise(text: str) -> list[float]:
    score_bandi = 1.0 if "반디" in text else 0.0
    score_kyungliu = 1.0 if "경류" in text else 0.0
    score_break = 1.0 if "격파" in text else 0.0
    return [score_bandi, score_kyungliu, score_break]
