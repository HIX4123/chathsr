from __future__ import annotations

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from chathsr.errors import ParseError
from chathsr.models import BoardPostRef, ParsedArticle
from chathsr.utils import (
    clean_inline_whitespace,
    collapse_blank_lines,
    dedupe_preserve_order,
    parse_post_id,
    stable_content_hash,
)


BLOCK_TAGS = {
    "article",
    "blockquote",
    "div",
    "dl",
    "dt",
    "dd",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}


def find_category_slug(html: str, *, board_url: str, category_label: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    board_path = urlparse(board_url).path
    target = clean_inline_whitespace(category_label)
    target_compact = target.replace(" ", "")
    for anchor in soup.select(".board-category a[href], a[href]"):
        label = clean_inline_whitespace(anchor.get_text(" ", strip=True))
        href = urljoin(board_url, anchor["href"])
        parsed = urlparse(href)
        if parsed.path != board_path:
            continue
        query = parse_qs(parsed.query)
        values = query.get("category")
        if values and values[0]:
            slug = values[0]
            label_compact = label.replace(" ", "")
            if (
                label == target
                or target_compact == label_compact
                or target_compact in label_compact
                or slug == target
            ):
                return slug
    raise ParseError(f"Could not find category slug for '{category_label}'.")


def build_category_page_url(board_url: str, *, category_slug: str, page: int = 1) -> str:
    separator = "&" if "?" in board_url else "?"
    return f"{board_url}{separator}{urlencode({'category': category_slug, 'p': page})}"


def parse_board_posts(html: str, *, board_url: str) -> list[BoardPostRef]:
    soup = BeautifulSoup(html, "html.parser")
    posts: list[BoardPostRef] = []
    seen_ids: set[int] = set()
    for anchor in soup.select("a.vrow[href]"):
        href = urljoin(board_url, anchor["href"])
        post_id = parse_post_id(href)
        if post_id is None or post_id in seen_ids:
            continue
        seen_ids.add(post_id)
        number_text = clean_inline_whitespace(
            anchor.select_one("span.vcol.col-id").get_text(" ", strip=True)
            if anchor.select_one("span.vcol.col-id")
            else ""
        )
        title_tag = anchor.select_one("span.title") or anchor.select_one(
            "span.vcol.col-title"
        )
        title = clean_inline_whitespace(title_tag.get_text(" ", strip=True)) if title_tag else ""
        created_tag = anchor.select_one("time[datetime]")
        posts.append(
            BoardPostRef(
                post_id=post_id,
                url=href,
                title=title,
                created_at=created_tag["datetime"] if created_tag else None,
                is_notice=number_text == "공지",
            )
        )
    return posts


def parse_article(html: str, *, url: str) -> ParsedArticle:
    soup = BeautifulSoup(html, "html.parser")
    content_root = soup.select_one("div.fr-view.article-content")
    if content_root is None:
        content_root = soup.select_one("div.article-content")
    if content_root is None:
        raise ParseError("Could not locate article content container.")

    title_root = soup.select_one("div.article-head div.title") or soup.select_one("div.title")
    if title_root is None:
        raise ParseError("Could not locate article title container.")

    category_label = None
    badge = title_root.select_one("span.badge")
    if badge:
        category_label = clean_inline_whitespace(badge.get_text(" ", strip=True))
        badge.extract()

    title = clean_inline_whitespace(title_root.get_text(" ", strip=True))
    if not title:
        raise ParseError("Article title is empty.")

    created_at = None
    for selector in (
        "div.article-info time[datetime]",
        "div.article-head time[datetime]",
        "time[datetime]",
    ):
        time_tag = soup.select_one(selector)
        if time_tag:
            created_at = time_tag["datetime"]
            break

    author = None
    for selector in (
        "div.article-head .user-info [data-filter]",
        "div.article-info .user-info [data-filter]",
        ".user-info [data-filter]",
        "[data-filter]",
    ):
        user_tag = soup.select_one(selector)
        if user_tag and user_tag.get("data-filter"):
            author = user_tag["data-filter"]
            break
    if author is None:
        for selector in ("div.article-head .user-info", ".user-info", ".article-author"):
            user_tag = soup.select_one(selector)
            if user_tag:
                author = clean_inline_whitespace(user_tag.get_text(" ", strip=True)) or None
                if author:
                    break

    image_urls = dedupe_preserve_order(
        urljoin(url, img["src"])
        for img in content_root.select("img[src]")
        if img.get("src")
    )
    video_urls = dedupe_preserve_order(
        urljoin(url, node["src"])
        for selector in ("iframe[src]", "video[src]", "source[src]", "embed[src]")
        for node in content_root.select(selector)
        if node.get("src")
    )
    body_text = extract_article_text(content_root)
    if video_urls:
        body_text = _prepend_video_lines(body_text, video_urls)
    if not body_text and not image_urls and not video_urls:
        raise ParseError("Article body is empty after normalization.")

    post_id = parse_post_id(url)
    if post_id is None:
        raise ParseError(f"Could not extract post id from URL: {url}")

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
        raw_html=html,
        content_hash=content_hash,
    )


def _prepend_video_lines(body_text: str, video_urls: list[str]) -> str:
    lines = ["동영상 임베드:", *video_urls]
    video_block = "\n".join(lines)
    if not body_text:
        return video_block
    return f"{video_block}\n\n{body_text}"


def extract_article_text(content_root: Tag) -> str:
    pieces: list[str] = []
    _append_text(content_root, pieces)
    text = "".join(pieces)
    text = collapse_blank_lines(text)
    return text


def _append_text(node: Tag | NavigableString, pieces: list[str]) -> None:
    if isinstance(node, NavigableString):
        text = str(node)
        if text.strip():
            pieces.append(text)
        return

    if not isinstance(node, Tag):
        return
    if node.name in {"script", "style", "noscript"}:
        return
    if node.name == "br":
        pieces.append("\n")
        return
    if node.name == "img":
        alt = clean_inline_whitespace(node.get("alt", ""))
        if alt:
            pieces.append(alt)
        return

    prefix = "- " if node.name == "li" else ""
    if prefix:
        pieces.append(prefix)
    for child in node.children:
        _append_text(child, pieces)
    if node.name in BLOCK_TAGS:
        pieces.append("\n")
