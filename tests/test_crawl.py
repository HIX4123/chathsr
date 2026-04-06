from __future__ import annotations

import json
from pathlib import Path

from chathsr.crawl import ArcaLiveCrawler
from chathsr.db import Database
from chathsr.parsing import build_category_page_url


FIXTURES = Path(__file__).parent / "fixtures"


def test_crawler_backfill_uses_injected_transport(settings) -> None:
    board_html = (FIXTURES / "board_page.html").read_text(encoding="utf-8")
    article_html = (FIXTURES / "article_page.html").read_text(encoding="utf-8")
    mapping = {
        settings.board_url: board_html,
        build_category_page_url(settings.board_url, category_slug="정보", page=1): board_html,
        "https://arca.live/b/hkstarrail/12345678": article_html,
        "https://arca.live/b/hkstarrail/12340000?foo=bar": article_html.replace(
            "반디 픽업 정리",
            "경류 세팅 요약",
        ),
    }
    crawler = ArcaLiveCrawler(
        settings,
        transport_factory=_dummy_transport_factory(mapping),
    )
    db = Database(settings.database_path)
    try:
        stats = crawler.crawl_backfill(
            db,
            max_pages=1,
        )
        post = db.get_post(12345678)
    finally:
        db.close()

    assert stats["pages"] == 1
    assert stats["articles"] == 2
    assert stats["new_posts"] == 2
    assert post is not None
    assert post["title"] == "반디 픽업 정리"


def test_crawler_sync_uses_injected_transport(settings) -> None:
    board_html = (FIXTURES / "board_page.html").read_text(encoding="utf-8")
    article_html = (FIXTURES / "article_page.html").read_text(encoding="utf-8")
    mapping = {
        settings.board_url: board_html,
        build_category_page_url(settings.board_url, category_slug="정보", page=1): board_html,
        "https://arca.live/b/hkstarrail/12345678": article_html,
        "https://arca.live/b/hkstarrail/12340000?foo=bar": article_html.replace(
            "반디 픽업 정리",
            "경류 세팅 요약",
        ),
    }
    crawler = ArcaLiveCrawler(
        settings,
        transport_factory=_dummy_transport_factory(mapping),
    )
    db = Database(settings.database_path)
    try:
        crawler.crawl_backfill(db, max_pages=1)
        stats = crawler.sync(
            db,
            max_pages=1,
            unchanged_limit=1,
        )
    finally:
        db.close()

    assert stats["pages"] == 1
    assert stats["articles"] == 1
    assert stats["new_posts"] == 0
    assert stats["changed_posts"] == 0


def test_crawler_backfill_saves_failed_posts_and_continues(settings) -> None:
    board_html = (FIXTURES / "board_page.html").read_text(encoding="utf-8")
    article_html = (FIXTURES / "article_page.html").read_text(encoding="utf-8")
    empty_article_html = """
    <html>
      <body>
        <div class="article-head">
          <div class="title">
            <span class="badge badge-success">정보</span>
            비어 있는 글
          </div>
        </div>
        <div class="article-content"></div>
      </body>
    </html>
    """
    mapping = {
        settings.board_url: board_html,
        build_category_page_url(settings.board_url, category_slug="정보", page=1): board_html,
        "https://arca.live/b/hkstarrail/12345678": article_html,
        "https://arca.live/b/hkstarrail/12340000?foo=bar": empty_article_html,
    }
    crawler = ArcaLiveCrawler(
        settings,
        transport_factory=_dummy_transport_factory(mapping),
    )
    db = Database(settings.database_path)
    try:
        stats = crawler.crawl_backfill(
            db,
            max_pages=1,
        )
        saved_post = db.get_post(12345678)
        missing_post = db.get_post(12340000)
    finally:
        db.close()

    failed_html = settings.failed_posts_dir / "12340000.html"
    failed_log = settings.failed_posts_dir / "failed_posts.jsonl"
    records = [json.loads(line) for line in failed_log.read_text(encoding="utf-8").splitlines()]

    assert stats["pages"] == 1
    assert stats["articles"] == 1
    assert stats["new_posts"] == 1
    assert stats["failed_articles"] == 1
    assert saved_post is not None
    assert missing_post is None
    assert failed_html.exists()
    assert failed_log.exists()
    assert records[0]["post_id"] == 12340000
    assert records[0]["saved_path"] == str(failed_html.resolve())


def test_crawler_incremental_articles_stop_at_since_post_id(settings) -> None:
    board_html = (FIXTURES / "board_page.html").read_text(encoding="utf-8")
    article_html = (FIXTURES / "article_page.html").read_text(encoding="utf-8")
    mapping = {
        settings.board_url: board_html,
        build_category_page_url(settings.board_url, category_slug="정보", page=1): board_html,
        "https://arca.live/b/hkstarrail/12345678": article_html,
    }
    crawler = ArcaLiveCrawler(
        settings,
        transport_factory=_dummy_transport_factory(mapping),
    )

    articles, stats = crawler.crawl_incremental_articles(
        since_post_id=12345000,
        recheck_posts=0,
        max_pages=1,
    )

    assert stats["pages"] == 1
    assert stats["articles"] == 1
    assert [article.post_id for article in articles] == [12345678]


def test_crawler_incremental_articles_rechecks_recent_posts(settings) -> None:
    board_html = (FIXTURES / "board_page.html").read_text(encoding="utf-8")
    article_html = (FIXTURES / "article_page.html").read_text(encoding="utf-8")
    mapping = {
        settings.board_url: board_html,
        build_category_page_url(settings.board_url, category_slug="정보", page=1): board_html,
        "https://arca.live/b/hkstarrail/12345678": article_html,
        "https://arca.live/b/hkstarrail/12340000?foo=bar": article_html.replace(
            "반디 픽업 정리",
            "경류 세팅 요약",
        ),
    }
    crawler = ArcaLiveCrawler(
        settings,
        transport_factory=_dummy_transport_factory(mapping),
    )

    articles, stats = crawler.crawl_incremental_articles(
        since_post_id=12345000,
        recheck_posts=2,
        max_pages=1,
    )

    assert stats["pages"] == 1
    assert stats["articles"] == 2
    assert [article.post_id for article in articles] == [12345678, 12340000]


def _dummy_transport_factory(mapping: dict[str, str]):
    def factory(
        settings,
        *,
        verbose: bool = False,
    ):
        return _DummyTransport(mapping)

    return factory


class _DummyTransport:
    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping

    def __enter__(self) -> _DummyTransport:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
        return None

    def fetch(self, url: str) -> str:
        return self.mapping[url]

    def close(self) -> None:
        return None
