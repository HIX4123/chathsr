from __future__ import annotations

from collections import defaultdict

from chathsr.config import Settings
from chathsr.db import Database
from chathsr.gemini_client import GeminiClient
from chathsr.models import RetrievedChunk
from chathsr.utils import cosine_similarity, decode_embedding


def retrieve_chunks(
    db: Database,
    settings: Settings,
    gemini: GeminiClient,
    *,
    question: str,
    top_k: int | None = None,
) -> list[RetrievedChunk]:
    target = top_k or settings.top_k
    candidate_limit = max(target * 4, 12)

    bm25_rows = db.bm25_search(question, limit=candidate_limit)
    bm25_rank = {row["chunk_id"]: rank for rank, row in enumerate(bm25_rows, start=1)}

    query_vector = gemini.embed_query(question)
    vector_candidates: list[tuple[str, float]] = []
    for row in db.iter_embeddings(
        embedding_model=settings.embedding_model,
        embedding_space_version=settings.embedding_space_version,
    ):
        score = cosine_similarity(query_vector, decode_embedding(row["embedding_blob"]))
        vector_candidates.append((row["chunk_id"], score))
    vector_candidates.sort(key=lambda item: item[1], reverse=True)
    vector_candidates = vector_candidates[:candidate_limit]
    vector_rank = {
        chunk_id: rank for rank, (chunk_id, _) in enumerate(vector_candidates, start=1)
    }

    fused_scores: dict[str, float] = defaultdict(float)
    for chunk_id, rank in bm25_rank.items():
        fused_scores[chunk_id] += _rrf(rank)
    for chunk_id, rank in vector_rank.items():
        fused_scores[chunk_id] += _rrf(rank)

    ranked_ids = [chunk_id for chunk_id, _ in sorted(
        fused_scores.items(),
        key=lambda item: item[1],
        reverse=True,
    )[:target]]
    rows = db.get_chunks_by_ids(ranked_ids)
    row_map = {row["chunk_id"]: row for row in rows}
    results: list[RetrievedChunk] = []
    for chunk_id in ranked_ids:
        row = row_map.get(chunk_id)
        if row is None:
            continue
        results.append(
            RetrievedChunk(
                chunk_id=row["chunk_id"],
                post_id=row["post_id"],
                ordinal=row["ordinal"],
                url=row["url"],
                title=row["title"],
                created_at=row["created_at"],
                chunk_text=row["chunk_text"],
                fused_score=fused_scores[chunk_id],
                bm25_rank=bm25_rank.get(chunk_id),
                vector_rank=vector_rank.get(chunk_id),
            )
        )
    return results


def answer_question(
    db: Database,
    settings: Settings,
    gemini: GeminiClient,
    *,
    question: str,
    top_k: int | None = None,
    use_cheap_model: bool = False,
) -> str:
    chunks = retrieve_chunks(db, settings, gemini, question=question, top_k=top_k)
    answer = gemini.generate_answer(
        question=question,
        chunks=chunks,
        use_cheap_model=use_cheap_model,
    )
    if not chunks:
        return answer
    source_lines = []
    seen_posts: set[int] = set()
    source_index = 1
    for chunk in chunks:
        if chunk.post_id in seen_posts:
            continue
        seen_posts.add(chunk.post_id)
        source_lines.append(
            f"[{source_index}] {chunk.title} | {chunk.created_at or '알 수 없음'} | {chunk.url}"
        )
        source_index += 1
    return f"{answer}\n\n출처:\n" + "\n".join(source_lines)


def _rrf(rank: int, *, k: int = 60) -> float:
    return 1.0 / (k + rank)
