from __future__ import annotations

from contextlib import contextmanager

from playwright.sync_api import Browser, BrowserContext, Page, TimeoutError, sync_playwright

from chathsr.config import Settings
from chathsr.db import Database
from chathsr.errors import CrawlBlockedError, StorageStateError
from chathsr.parsing import (
    build_category_page_url,
    find_category_slug,
    parse_article,
    parse_board_posts,
)
from chathsr.utils import utc_now_iso


class ArcaLiveCrawler:
    def __init__(self, settings: Settings):
        self.settings = settings

    def authenticate(self) -> None:
        with self._browser_session(headless=False, force_persistent=True) as page:
            page.goto(self.settings.board_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)
            print("브라우저가 열렸습니다. Cloudflare 통과와 ArcaLive 로그인을 완료한 뒤 Enter를 누르세요.")
            input()

    def crawl_backfill(
        self,
        db: Database,
        *,
        max_pages: int | None = None,
        headless: bool = True,
    ) -> dict[str, int]:
        stats = {"pages": 0, "articles": 0, "new_posts": 0, "changed_posts": 0}
        with self._browser_session(headless=headless) as page:
            category_slug = self._resolve_category_slug(page)
            db.set_crawl_state("category_slug", category_slug)
            seen_ids: set[int] = set()
            page_number = 1
            while True:
                if max_pages is not None and page_number > max_pages:
                    break
                board_html = self._fetch_html(
                    page,
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
                    article_html = self._fetch_html(page, ref.url)
                    article = parse_article(article_html, url=ref.url)
                    is_new, changed = db.upsert_article(article)
                    if is_new:
                        stats["new_posts"] += 1
                    if changed:
                        stats["changed_posts"] += 1
                    stats["articles"] += 1
                    seen_ids.add(ref.post_id)
                page_number += 1
        db.set_crawl_state("last_backfill_at", utc_now_iso())
        return stats

    def sync(
        self,
        db: Database,
        *,
        max_pages: int | None = None,
        headless: bool = True,
        unchanged_limit: int = 20,
    ) -> dict[str, int]:
        stats = {"pages": 0, "articles": 0, "new_posts": 0, "changed_posts": 0}
        with self._browser_session(headless=headless) as page:
            category_slug = self._resolve_category_slug(page)
            db.set_crawl_state("category_slug", category_slug)
            unchanged_streak = 0
            seen_ids: set[int] = set()
            page_number = 1
            should_stop = False
            while not should_stop:
                if max_pages is not None and page_number > max_pages:
                    break
                board_html = self._fetch_html(
                    page,
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
                    article_html = self._fetch_html(page, ref.url)
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

    def _resolve_category_slug(self, page: Page) -> str:
        board_html = self._fetch_html(page, self.settings.board_url)
        return find_category_slug(
            board_html,
            board_url=self.settings.board_url,
            category_label=self.settings.category_label,
        )

    @contextmanager
    def _browser_session(self, *, headless: bool, force_persistent: bool = False) -> Page:
        with sync_playwright() as playwright:
            browser: Browser | None = None
            if not force_persistent and self.settings.should_use_storage_state:
                context, browser = self._launch_storage_state_context(
                    playwright,
                    headless=headless,
                )
            else:
                context = self._launch_persistent_context(playwright, headless=headless)
            try:
                page = self._get_primary_page(context)
                page.set_default_timeout(60000)
                yield page
            finally:
                context.close()
                if browser is not None:
                    browser.close()

    def _launch_persistent_context(self, playwright, *, headless: bool) -> BrowserContext:
        return playwright.chromium.launch_persistent_context(
            str(self.settings.playwright_profile_dir),
            headless=headless,
        )

    def _launch_storage_state_context(
        self,
        playwright,
        *,
        headless: bool,
    ) -> tuple[BrowserContext, Browser]:
        storage_state_path = self.settings.playwright_storage_state_path
        if not storage_state_path.exists():
            if self.settings.playwright_storage_state_path_configured:
                raise StorageStateError(
                    "PLAYWRIGHT_STORAGE_STATE_PATH points to a missing file: "
                    f"{storage_state_path}"
                )
            raise StorageStateError(
                "No storage state file is available. Run `rag import-state <path>` "
                "or unset PLAYWRIGHT_STORAGE_STATE_PATH to use `rag auth`."
            )
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=str(storage_state_path))
        return context, browser

    def _get_primary_page(self, context: BrowserContext) -> Page:
        if context.pages:
            return context.pages[0]
        return context.new_page()

    def _fetch_html(self, page: Page, url: str) -> str:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)
        except TimeoutError as exc:
            raise CrawlBlockedError(f"Timed out while loading {url}") from exc
        html = page.content()
        if "Just a moment..." in html or "Enable JavaScript and cookies to continue" in html:
            if self.settings.should_use_storage_state:
                raise CrawlBlockedError(
                    "ArcaLive blocked the current browser session. The imported "
                    "state file is missing, expired, or incomplete. Export a fresh "
                    "Playwright state or browser cookie JSON on an external machine "
                    "and run `rag import-state <path>` again."
                )
            raise CrawlBlockedError(
                "ArcaLive blocked the current browser session. Run `rag auth` to refresh the saved profile."
            )
        return html
