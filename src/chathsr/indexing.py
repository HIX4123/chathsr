from __future__ import annotations

import sys

from chathsr.chunking import chunk_article
from chathsr.config import Settings
from chathsr.db import Database
from chathsr.errors import EmbeddingSpaceMismatchError
from chathsr.gemini_client import GeminiClient
from chathsr.models import ParsedArticle


def index_posts(
    db: Database,
    settings: Settings,
    gemini: GeminiClient,
    *,
    changed_only: bool,
    full_reembed: bool,
    verbose: bool = False,
) -> int:
    existing_spaces = db.get_existing_embedding_spaces()
    expected_space = {
        (
            settings.embedding_model,
            settings.embedding_space_version,
            settings.embedding_dim,
        )
    }
    if existing_spaces and existing_spaces != expected_space and not full_reembed:
        raise EmbeddingSpaceMismatchError(
            "Existing embeddings use a different embedding model/version. "
            "Run `rag index full-reembed` to rebuild the vector store."
        )
    if full_reembed:
        if verbose:
            print("[index] clearing existing chunk embeddings", file=sys.stderr, flush=True)
        db.clear_chunks()
    target_posts = db.select_posts_for_indexing(changed_only=changed_only and not full_reembed)
    if verbose:
        print(f"[index] selected {len(target_posts)} post(s) for indexing", file=sys.stderr, flush=True)
    indexed_count = 0
    for row in target_posts:
        article = ParsedArticle(
            post_id=row["post_id"],
            url=row["url"],
            title=row["title"],
            category_label=row["category_label"],
            created_at=row["created_at"],
            author=row["author"],
            body_text=row["body_text"],
            image_urls=[],
            raw_html=row["raw_html"] or "",
            content_hash=row["content_hash"],
        )
        chunks = chunk_article(article)
        embeddings = gemini.embed_document_chunks(
            [chunk.chunk_text for chunk in chunks],
            title=article.title,
        )
        db.replace_post_chunks(
            post_id=article.post_id,
            title=article.title,
            content_hash=article.content_hash,
            chunks=chunks,
            embeddings=embeddings,
            embedding_model=settings.embedding_model,
            embedding_space_version=settings.embedding_space_version,
            embedding_dim=settings.embedding_dim,
        )
        indexed_count += 1
        if verbose:
            print(
                f"[index] indexed post_id={article.post_id} chunks={len(chunks)}",
                file=sys.stderr,
                flush=True,
            )
    return indexed_count
