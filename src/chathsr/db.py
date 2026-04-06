from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from chathsr.models import ChunkRecord, ParsedArticle
from chathsr.utils import encode_embedding, tokenise_search_terms, utc_now_iso


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS posts (
    post_id INTEGER PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    category_label TEXT,
    created_at TEXT,
    author TEXT,
    body_text TEXT,
    image_urls_json TEXT NOT NULL DEFAULT '[]',
    video_urls_json TEXT NOT NULL DEFAULT '[]',
    raw_html TEXT,
    content_hash TEXT NOT NULL,
    remote_synced_content_hash TEXT,
    remote_synced_at TEXT,
    indexed_content_hash TEXT,
    indexed_at TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    crawled_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    post_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding_blob BLOB NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding_space_version TEXT NOT NULL,
    embedding_dim INTEGER NOT NULL,
    indexed_at TEXT NOT NULL,
    FOREIGN KEY(post_id) REFERENCES posts(post_id) ON DELETE CASCADE,
    UNIQUE(post_id, ordinal, embedding_model, embedding_space_version)
);

CREATE TABLE IF NOT EXISTS crawl_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS processed_sync_batches (
    batch_id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    status TEXT NOT NULL,
    article_count INTEGER NOT NULL,
    processed_at TEXT NOT NULL,
    error_detail TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
CREATE INDEX IF NOT EXISTS idx_posts_content_hash ON posts(content_hash);
CREATE INDEX IF NOT EXISTS idx_chunks_post_id ON chunks(post_id);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_space ON chunks(embedding_model, embedding_space_version);
CREATE INDEX IF NOT EXISTS idx_processed_sync_batches_status ON processed_sync_batches(status);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    post_id UNINDEXED,
    title,
    chunk_text,
    tokenize='unicode61'
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate_posts_schema()

    def close(self) -> None:
        self.conn.close()

    def record_run_start(self, command: str, detail: str = "") -> int:
        now = utc_now_iso()
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO runs(command, status, detail, started_at)
                VALUES (?, 'running', ?, ?)
                """,
                (command, detail, now),
            )
        return int(cursor.lastrowid)

    def record_run_finish(self, run_id: int, *, status: str, detail: str = "") -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE runs
                SET status = ?, detail = ?, finished_at = ?
                WHERE id = ?
                """,
                (status, detail, utc_now_iso(), run_id),
            )

    def set_crawl_state(self, key: str, value: str) -> None:
        now = utc_now_iso()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO crawl_state(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, now),
            )

    def get_crawl_state(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM crawl_state WHERE key = ?",
            (key,),
        ).fetchone()
        return row["value"] if row else None

    def get_sync_batch_status(self, batch_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT status FROM processed_sync_batches WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
        return row["status"] if row else None

    def get_latest_post_summary(self) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT post_id, crawled_at
            FROM posts
            ORDER BY post_id DESC
            LIMIT 1
            """
        ).fetchone()

    def get_latest_successful_sync_batch_id(self) -> str | None:
        row = self.conn.execute(
            """
            SELECT batch_id
            FROM processed_sync_batches
            WHERE status = 'succeeded'
            ORDER BY processed_at DESC, batch_id DESC
            LIMIT 1
            """
        ).fetchone()
        return row["batch_id"] if row else None

    def list_recent_posts(self, *, limit: int) -> list[sqlite3.Row]:
        if limit <= 0:
            return []
        return list(
            self.conn.execute(
                """
                SELECT post_id, content_hash
                FROM posts
                ORDER BY post_id DESC
                LIMIT ?
                """,
                (limit,),
            )
        )

    def record_sync_batch(
        self,
        *,
        batch_id: str,
        source_name: str,
        status: str,
        article_count: int,
        error_detail: str = "",
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO processed_sync_batches(
                    batch_id, source_name, status, article_count, processed_at, error_detail
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(batch_id) DO UPDATE SET
                    source_name = excluded.source_name,
                    status = excluded.status,
                    article_count = excluded.article_count,
                    processed_at = excluded.processed_at,
                    error_detail = excluded.error_detail
                """,
                (
                    batch_id,
                    source_name,
                    status,
                    article_count,
                    utc_now_iso(),
                    error_detail,
                ),
            )

    def get_post(self, post_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM posts WHERE post_id = ?",
            (post_id,),
        ).fetchone()

    def upsert_article(self, article: ParsedArticle) -> tuple[bool, bool]:
        existing = self.get_post(article.post_id)
        is_new = existing is None
        changed = (
            is_new
            or existing["content_hash"] != article.content_hash
            or existing["status"] != "active"
        )
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO posts(
                    post_id, url, title, category_label, created_at, author, body_text,
                    image_urls_json, video_urls_json, raw_html, content_hash, status, crawled_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
                ON CONFLICT(post_id) DO UPDATE SET
                    url = excluded.url,
                    title = excluded.title,
                    category_label = excluded.category_label,
                    created_at = excluded.created_at,
                    author = excluded.author,
                    body_text = excluded.body_text,
                    image_urls_json = excluded.image_urls_json,
                    video_urls_json = excluded.video_urls_json,
                    raw_html = excluded.raw_html,
                    content_hash = excluded.content_hash,
                    status = 'active',
                    crawled_at = excluded.crawled_at
                """,
                (
                    article.post_id,
                    article.url,
                    article.title,
                    article.category_label,
                    article.created_at,
                    article.author,
                    article.body_text,
                    _json_dumps(article.image_urls),
                    _json_dumps(article.video_urls),
                    article.raw_html,
                    article.content_hash,
                    utc_now_iso(),
                ),
            )
        return is_new, changed

    def select_posts_for_indexing(self, *, changed_only: bool) -> list[sqlite3.Row]:
        conditions = [
            "status = 'active'",
            "body_text IS NOT NULL",
            "body_text != ''",
        ]
        if changed_only:
            conditions.append(
                "(indexed_content_hash IS NULL OR indexed_content_hash != content_hash)"
            )
        sql = f"""
            SELECT *
            FROM posts
            WHERE {' AND '.join(conditions)}
            ORDER BY datetime(created_at) DESC, post_id DESC
        """
        return list(self.conn.execute(sql))

    def select_posts_pending_remote_sync(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT *
                FROM posts
                WHERE status = 'active'
                  AND (
                    remote_synced_content_hash IS NULL
                    OR remote_synced_content_hash != content_hash
                  )
                ORDER BY datetime(created_at) DESC, post_id DESC
                """
            )
        )

    def mark_posts_remote_synced(
        self,
        synced_posts: Iterable[tuple[int, str]],
    ) -> int:
        synced_at = utc_now_iso()
        updated = 0
        with self.conn:
            for post_id, content_hash in synced_posts:
                cursor = self.conn.execute(
                    """
                    UPDATE posts
                    SET remote_synced_content_hash = ?, remote_synced_at = ?
                    WHERE post_id = ? AND content_hash = ? AND status = 'active'
                    """,
                    (content_hash, synced_at, post_id, content_hash),
                )
                updated += max(cursor.rowcount, 0)
        return updated

    def get_existing_embedding_spaces(self) -> set[tuple[str, str, int]]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT embedding_model, embedding_space_version, embedding_dim
            FROM chunks
            """
        ).fetchall()
        return {
            (row["embedding_model"], row["embedding_space_version"], row["embedding_dim"])
            for row in rows
        }

    def clear_chunks(self) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM chunks")
            self.conn.execute("DELETE FROM chunks_fts")
            self.conn.execute(
                "UPDATE posts SET indexed_content_hash = NULL, indexed_at = NULL"
            )

    def replace_post_chunks(
        self,
        *,
        post_id: int,
        title: str,
        content_hash: str,
        chunks: list[ChunkRecord],
        embeddings: list[list[float]],
        embedding_model: str,
        embedding_space_version: str,
        embedding_dim: int,
    ) -> None:
        indexed_at = utc_now_iso()
        with self.conn:
            self.conn.execute("DELETE FROM chunks WHERE post_id = ?", (post_id,))
            self.conn.execute("DELETE FROM chunks_fts WHERE post_id = ?", (str(post_id),))
            for chunk, embedding in zip(chunks, embeddings, strict=True):
                self.conn.execute(
                    """
                    INSERT INTO chunks(
                        chunk_id, post_id, ordinal, chunk_text, embedding_blob,
                        embedding_model, embedding_space_version, embedding_dim, indexed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.chunk_id,
                        chunk.post_id,
                        chunk.ordinal,
                        chunk.chunk_text,
                        encode_embedding(embedding),
                        embedding_model,
                        embedding_space_version,
                        embedding_dim,
                        indexed_at,
                    ),
                )
                self.conn.execute(
                    """
                    INSERT INTO chunks_fts(chunk_id, post_id, title, chunk_text)
                    VALUES (?, ?, ?, ?)
                    """,
                    (chunk.chunk_id, str(chunk.post_id), title, chunk.chunk_text),
                )
            self.conn.execute(
                """
                UPDATE posts
                SET indexed_content_hash = ?, indexed_at = ?
                WHERE post_id = ?
                """,
                (content_hash, indexed_at, post_id),
            )

    def bm25_search(self, question: str, *, limit: int) -> list[sqlite3.Row]:
        terms = tokenise_search_terms(question)
        if not terms:
            return []
        query = " OR ".join(f'"{term}"' for term in terms)
        return list(
            self.conn.execute(
                """
                SELECT
                    c.chunk_id,
                    c.post_id,
                    c.ordinal,
                    p.url,
                    p.title,
                    p.created_at,
                    c.chunk_text,
                    bm25(chunks_fts) AS bm25_score
                FROM chunks_fts
                JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id
                JOIN posts p ON p.post_id = c.post_id
                WHERE chunks_fts MATCH ? AND p.status = 'active'
                ORDER BY bm25_score, p.post_id DESC
                LIMIT ?
                """,
                (query, limit),
            )
        )

    def iter_embeddings(
        self, *, embedding_model: str, embedding_space_version: str
    ) -> Iterable[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT
                c.chunk_id,
                c.post_id,
                c.ordinal,
                p.url,
                p.title,
                p.created_at,
                c.chunk_text,
                c.embedding_blob
            FROM chunks c
            JOIN posts p ON p.post_id = c.post_id
            WHERE p.status = 'active'
              AND c.embedding_model = ?
              AND c.embedding_space_version = ?
            """,
            (embedding_model, embedding_space_version),
        )

    def get_chunks_by_ids(self, chunk_ids: list[str]) -> list[sqlite3.Row]:
        if not chunk_ids:
            return []
        placeholders = ", ".join("?" for _ in chunk_ids)
        return list(
            self.conn.execute(
                f"""
                SELECT
                    c.chunk_id,
                    c.post_id,
                    c.ordinal,
                    p.url,
                    p.title,
                    p.created_at,
                    c.chunk_text
                FROM chunks c
                JOIN posts p ON p.post_id = c.post_id
                WHERE c.chunk_id IN ({placeholders})
                """,
                tuple(chunk_ids),
            )
        )

    def count_posts(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS count FROM posts").fetchone()
        return int(row["count"])

    def count_chunks(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()
        return int(row["count"])

    def _migrate_posts_schema(self) -> None:
        columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(posts)").fetchall()
        }
        with self.conn:
            if "video_urls_json" not in columns:
                self.conn.execute(
                    "ALTER TABLE posts ADD COLUMN video_urls_json TEXT NOT NULL DEFAULT '[]'"
                )
            if "remote_synced_content_hash" not in columns:
                self.conn.execute(
                    "ALTER TABLE posts ADD COLUMN remote_synced_content_hash TEXT"
                )
            if "remote_synced_at" not in columns:
                self.conn.execute("ALTER TABLE posts ADD COLUMN remote_synced_at TEXT")


def _json_dumps(values: list[str]) -> str:
    import json

    return json.dumps(values, ensure_ascii=False)
