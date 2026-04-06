from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class BoardPostRef:
    post_id: int
    url: str
    title: str
    created_at: str | None
    is_notice: bool


@dataclass(slots=True)
class ParsedArticle:
    post_id: int
    url: str
    title: str
    category_label: str | None
    created_at: str | None
    author: str | None
    body_text: str
    image_urls: list[str]
    video_urls: list[str]
    raw_html: str
    content_hash: str


@dataclass(slots=True)
class ChunkRecord:
    chunk_id: str
    post_id: int
    ordinal: int
    chunk_text: str


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: str
    post_id: int
    ordinal: int
    url: str
    title: str
    created_at: str | None
    chunk_text: str
    fused_score: float
    bm25_rank: int | None
    vector_rank: int | None
