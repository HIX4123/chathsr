from __future__ import annotations

from chathsr.models import ChunkRecord, ParsedArticle
from chathsr.utils import make_chunk_id


def chunk_article(
    article: ParsedArticle,
    *,
    target_chars: int = 1100,
    overlap_chars: int = 150,
) -> list[ChunkRecord]:
    header = build_chunk_header(article)
    paragraphs = _split_paragraphs(article.body_text, target_chars=target_chars)
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if current and len(candidate) > target_chars:
            chunks.append(current)
            overlap = current[-overlap_chars:].strip()
            current = paragraph if not overlap else f"{overlap}\n\n{paragraph}"
        else:
            current = candidate

    if current:
        chunks.append(current)

    return [
        ChunkRecord(
            chunk_id=make_chunk_id(article.post_id, ordinal, f"{header}\n\n{chunk_text}"),
            post_id=article.post_id,
            ordinal=ordinal,
            chunk_text=f"{header}\n\n{chunk_text}".strip(),
        )
        for ordinal, chunk_text in enumerate(chunks)
    ]


def build_chunk_header(article: ParsedArticle) -> str:
    lines = [f"제목: {article.title}"]
    if article.category_label:
        lines.append(f"카테고리: {article.category_label}")
    if article.created_at:
        lines.append(f"작성일: {article.created_at}")
    return "\n".join(lines)


def _split_paragraphs(body_text: str, *, target_chars: int) -> list[str]:
    paragraphs = [paragraph.strip() for paragraph in body_text.split("\n\n") if paragraph.strip()]
    results: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= target_chars:
            results.append(paragraph)
            continue
        start = 0
        while start < len(paragraph):
            end = min(start + target_chars, len(paragraph))
            results.append(paragraph[start:end].strip())
            start = end
    return results
