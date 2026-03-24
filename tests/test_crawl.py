from __future__ import annotations

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
            transport_name="custom-http",
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
        crawler.crawl_backfill(db, max_pages=1, transport_name="custom-http")
        stats = crawler.sync(
            db,
            max_pages=1,
            unchanged_limit=1,
            transport_name="custom-http",
        )
    finally:
        db.close()

    assert stats["pages"] == 1
    assert stats["articles"] == 1
    assert stats["new_posts"] == 0
    assert stats["changed_posts"] == 0


def _dummy_transport_factory(mapping: dict[str, str]):
    def factory(settings, transport_name: str, *, headless: bool = True, force_persistent: bool = False):
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
