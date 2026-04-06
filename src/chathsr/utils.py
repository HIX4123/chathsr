from __future__ import annotations

import hashlib
import math
import re
from array import array
from datetime import UTC, datetime
from typing import Iterable
from urllib.parse import urlparse


POST_ID_RE = re.compile(r"/(\d+)(?:\?|$)")
TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣_]+")


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_content_hash(
    *,
    title: str,
    category_label: str | None,
    created_at: str | None,
    author: str | None,
    body_text: str,
    image_urls: Iterable[str],
    video_urls: Iterable[str],
) -> str:
    image_block = "\n".join(image_urls)
    video_block = "\n".join(video_urls)
    pieces = [
        title.strip(),
        (category_label or "").strip(),
        (created_at or "").strip(),
        (author or "").strip(),
        body_text.strip(),
        image_block,
    ]
    if video_block:
        pieces.append(video_block)
    return sha256_text("\n---\n".join(pieces))


def collapse_blank_lines(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_inline_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def parse_post_id(url: str) -> int | None:
    parsed = urlparse(url)
    target = parsed.path
    if parsed.query:
        target = f"{target}?{parsed.query}"
    match = POST_ID_RE.search(target)
    if match:
        return int(match.group(1))
    return None


def make_chunk_id(post_id: int, ordinal: int, chunk_text: str) -> str:
    digest = sha256_text(f"{post_id}:{ordinal}:{chunk_text}")[:16]
    return f"{post_id}:{ordinal}:{digest}"


def encode_embedding(values: Iterable[float]) -> bytes:
    buf = array("f", values)
    return buf.tobytes()


def decode_embedding(blob: bytes) -> list[float]:
    buf = array("f")
    buf.frombytes(blob)
    return list(buf)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def tokenise_search_terms(text: str) -> list[str]:
    return TOKEN_RE.findall(text)
