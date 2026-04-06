from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable

from chathsr.db import Database
from chathsr.errors import ImportFormatError
from chathsr.models import ParsedArticle
from chathsr.utils import stable_content_hash


def export_articles_jsonl(path: str | Path, articles: Iterable[ParsedArticle]) -> int:
    output_path = Path(path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output_path.open("w", encoding="utf-8") as handle:
        for article in articles:
            handle.write(json.dumps(_article_to_payload(article), ensure_ascii=False))
            handle.write("\n")
            count += 1
    return count


def import_articles_jsonl(
    path: str | Path,
    db: Database,
    *,
    verbose: bool = False,
) -> dict[str, int]:
    source_path = Path(path).resolve()
    files = _resolve_import_files(source_path)
    stats = {"files": 0, "articles": 0, "new_posts": 0, "changed_posts": 0}
    if verbose:
        print(f"[import] found {len(files)} jsonl file(s) under {source_path}", file=sys.stderr, flush=True)
    for file_path in files:
        stats["files"] += 1
        if verbose:
            print(f"[import] processing {file_path}", file=sys.stderr, flush=True)
        for article in _load_articles_jsonl(file_path):
            is_new, changed = db.upsert_article(article)
            stats["articles"] += 1
            if is_new:
                stats["new_posts"] += 1
            if changed:
                stats["changed_posts"] += 1
        if verbose:
            print(
                f"[import] done {file_path}: total_articles={stats['articles']}",
                file=sys.stderr,
                flush=True,
            )
    return stats


def _resolve_import_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if path.is_dir():
        files = sorted(item for item in path.glob("*.jsonl") if item.is_file())
        if files:
            return files
        raise ImportFormatError(f"No .jsonl files were found under {path}")
    raise ImportFormatError(f"Import path does not exist: {path}")


def _load_articles_jsonl(path: Path) -> list[ParsedArticle]:
    articles: list[ParsedArticle] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ImportFormatError(
                    f"{path}: line {line_number} is not valid JSON: {exc}"
                ) from exc
            articles.append(_payload_to_article(payload, path=path, line_number=line_number))
    return articles


def _payload_to_article(
    payload: object,
    *,
    path: Path,
    line_number: int,
) -> ParsedArticle:
    if not isinstance(payload, dict):
        raise ImportFormatError(
            f"{path}: line {line_number} must be a JSON object, got {type(payload).__name__}"
        )
    try:
        post_id = int(payload["post_id"])
        url = str(payload["url"]).strip()
        title = str(payload["title"]).strip()
        body_text = str(payload["body_text"])
    except KeyError as exc:
        raise ImportFormatError(
            f"{path}: line {line_number} is missing required field {exc.args[0]!r}"
        ) from exc
    if not url or not title:
        raise ImportFormatError(
            f"{path}: line {line_number} must include non-empty 'url' and 'title' values"
        )
    image_urls = payload.get("image_urls", [])
    if not isinstance(image_urls, list) or any(
        not isinstance(value, str) for value in image_urls
    ):
        raise ImportFormatError(
            f"{path}: line {line_number} field 'image_urls' must be a list of strings"
        )
    video_urls = payload.get("video_urls", [])
    if not isinstance(video_urls, list) or any(
        not isinstance(value, str) for value in video_urls
    ):
        raise ImportFormatError(
            f"{path}: line {line_number} field 'video_urls' must be a list of strings"
        )
    category_label = _optional_string(payload.get("category_label"))
    created_at = _optional_string(payload.get("created_at"))
    author = _optional_string(payload.get("author"))
    raw_html = _optional_string(payload.get("raw_html")) or ""
    content_hash = stable_content_hash(
        title=title,
        category_label=category_label,
        created_at=created_at,
        author=author,
        body_text=body_text,
        image_urls=image_urls,
        video_urls=video_urls,
    )
    return ParsedArticle(
        post_id=post_id,
        url=url,
        title=title,
        category_label=category_label,
        created_at=created_at,
        author=author,
        body_text=body_text,
        image_urls=image_urls,
        video_urls=video_urls,
        raw_html=raw_html,
        content_hash=content_hash,
    )


def _article_to_payload(article: ParsedArticle) -> dict[str, object]:
    return {
        "post_id": article.post_id,
        "url": article.url,
        "title": article.title,
        "category_label": article.category_label,
        "created_at": article.created_at,
        "author": article.author,
        "body_text": article.body_text,
        "image_urls": article.image_urls,
        "video_urls": article.video_urls,
        "raw_html": article.raw_html,
        "content_hash": article.content_hash,
    }


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
