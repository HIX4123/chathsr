from __future__ import annotations

from collections.abc import Callable

from chathsr.config import Settings
from chathsr.db import Database
from chathsr.models import ParsedArticle
from chathsr.parsing import (
    build_category_page_url,
    find_category_slug,
    parse_article,
    parse_board_posts,
)
from chathsr.transports import (
    DEFAULT_TRANSPORT,
    BrowserTransport,
    FetchTransport,
    create_transport,
)
from chathsr.utils import utc_now_iso


TransportFactory = Callable[..., FetchTransport]


class ArcaLiveCrawler:
    def __init__(
        self,
        settings: Settings,
        *,
        transport_factory: TransportFactory = create_transport,
    ) -> None:
        self.settings = settings
        self.transport_factory = transport_factory

    def authenticate(self) -> None:
        with BrowserTransport(
            self.settings,
            headless=False,
            force_persistent=True,
        ) as transport:
            transport.interactive_auth()

    def crawl_backfill(
        self,
        db: Database,
        *,
        max_pages: int | None = None,
        headless: bool = True,
        transport_name: str = DEFAULT_TRANSPORT,
    ) -> dict[str, int]:
        stats = {"pages": 0, "articles": 0, "new_posts": 0, "changed_posts": 0}
        with self.transport_factory(
            self.settings,
            transport_name,
            headless=headless,
        ) as transport:
            category_slug = self._resolve_category_slug(transport)
            db.set_crawl_state("category_slug", category_slug)
            articles = self._collect_backfill_articles(
                transport,
                category_slug=category_slug,
                max_pages=max_pages,
                stats=stats,
            )
            for article in articles:
                is_new, changed = db.upsert_article(article)
                if is_new:
                    stats["new_posts"] += 1
                if changed:
                    stats["changed_posts"] += 1
                stats["articles"] += 1
        db.set_crawl_state("last_backfill_at", utc_now_iso())
        return stats

    def crawl_backfill_articles(
        self,
        *,
        max_pages: int | None = None,
        headless: bool = True,
        transport_name: str = DEFAULT_TRANSPORT,
    ) -> tuple[list[ParsedArticle], dict[str, int]]:
        stats = {"pages": 0, "articles": 0, "new_posts": 0, "changed_posts": 0}
        with self.transport_factory(
            self.settings,
            transport_name,
            headless=headless,
        ) as transport:
            category_slug = self._resolve_category_slug(transport)
            articles = self._collect_backfill_articles(
                transport,
                category_slug=category_slug,
                max_pages=max_pages,
                stats=stats,
            )
        stats["articles"] = len(articles)
        return articles, stats

    def sync(
        self,
        db: Database,
        *,
        max_pages: int | None = None,
        headless: bool = True,
        unchanged_limit: int = 20,
        transport_name: str = DEFAULT_TRANSPORT,
    ) -> dict[str, int]:
        stats = {"pages": 0, "articles": 0, "new_posts": 0, "changed_posts": 0}
        with self.transport_factory(
            self.settings,
            transport_name,
            headless=headless,
        ) as transport:
            category_slug = self._resolve_category_slug(transport)
            db.set_crawl_state("category_slug", category_slug)
            unchanged_streak = 0
            seen_ids: set[int] = set()
            page_number = 1
            should_stop = False
            while not should_stop:
                if max_pages is not None and page_number > max_pages:
                    break
                board_html = self._fetch_html(
                    transport,
                    build_category_page_url(
                        self.settings.board_url,
                        category_slug=category_slug,
                        page=page_number,
                    ),
                )
                refs = [
                    ref
                    for ref in parse_board_posts(board_html, board_url=self.settings.board_url)
                    if not ref.is_notice and ref.post_id not in seen_ids
                ]
                if not refs:
                    break
                stats["pages"] += 1
                for ref in refs:
                    article_html = self._fetch_html(transport, ref.url)
                    article = parse_article(article_html, url=ref.url)
                    is_new, changed = db.upsert_article(article)
                    if is_new:
                        stats["new_posts"] += 1
                    if changed:
                        stats["changed_posts"] += 1
                        unchanged_streak = 0
                    else:
                        unchanged_streak += 1
                    stats["articles"] += 1
                    seen_ids.add(ref.post_id)
                    if unchanged_streak >= unchanged_limit:
                        should_stop = True
                        break
                page_number += 1
        db.set_crawl_state("last_sync_at", utc_now_iso())
        return stats

    def _resolve_category_slug(self, transport: FetchTransport) -> str:
        board_html = self._fetch_html(transport, self.settings.board_url)
        return find_category_slug(
            board_html,
            board_url=self.settings.board_url,
            category_label=self.settings.category_label,
        )

    def _collect_backfill_articles(
        self,
        transport: FetchTransport,
        *,
        category_slug: str,
        max_pages: int | None,
        stats: dict[str, int],
    ) -> list[ParsedArticle]:
        seen_ids: set[int] = set()
        page_number = 1
        articles: list[ParsedArticle] = []
        while True:
            if max_pages is not None and page_number > max_pages:
                break
            board_html = self._fetch_html(
                transport,
                build_category_page_url(
                    self.settings.board_url,
                    category_slug=category_slug,
                    page=page_number,
                ),
            )
            refs = [
                ref
                for ref in parse_board_posts(board_html, board_url=self.settings.board_url)
                if not ref.is_notice and ref.post_id not in seen_ids
            ]
            if not refs:
                break
            stats["pages"] += 1
            for ref in refs:
                article_html = self._fetch_html(transport, ref.url)
                article = parse_article(article_html, url=ref.url)
                articles.append(article)
                seen_ids.add(ref.post_id)
            page_number += 1
        return articles

    def _fetch_html(self, transport: FetchTransport, url: str) -> str:
        return transport.fetch(url)
