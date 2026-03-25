from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, Literal, Protocol, Self

from chathsr.config import Settings
from chathsr.custom_transport import CustomHTTPTransport
from chathsr.errors import (
    BrowserSessionError,
    CrawlBlockedError,
    StorageStateError,
    UnsupportedTransportError,
)

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page
else:
    Browser = BrowserContext = Page = Any


DEFAULT_TRANSPORT = "browser"
SUPPORTED_TRANSPORTS = ("browser", "custom-http")
TransportName = Literal["browser", "custom-http"]


class FetchTransport(Protocol):
    def fetch(self, url: str) -> str: ...

    def close(self) -> None: ...

    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type, exc, tb) -> None: ...


def _load_playwright_sync_api():
    try:
        from playwright.sync_api import TimeoutError, sync_playwright
    except ImportError as exc:
        raise BrowserSessionError(
            "Browser transport requires Playwright's sync API, but it could not be "
            "imported in this Python environment. If you only need websocket probe "
            "commands, they can run without browser transport. If you need browser "
            "crawl/auth commands, repair the local Playwright/greenlet runtime first."
        ) from exc
    return TimeoutError, sync_playwright


class BrowserTransport:
    def __init__(
        self,
        settings: Settings,
        *,
        headless: bool = True,
        force_persistent: bool = False,
        verbose: bool = False,
    ) -> None:
        self.settings = settings
        self.headless = headless
        self.force_persistent = force_persistent
        self.verbose = verbose
        self._mode = "profile"
        self._timeout_error, sync_playwright = _load_playwright_sync_api()
        self._playwright_manager = sync_playwright()
        self._playwright = self._playwright_manager.start()
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._close_context = True
        self._close_browser = False
        self._close_page = False
        self._closed = False
        try:
            self._start()
        except Exception:
            self.close()
            raise

    def __enter__(self) -> BrowserTransport:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
        return None

    def interactive_auth(self) -> None:
        page = self._require_page()
        self._emit_verbose(f"AUTH open {self.settings.board_url}")
        page.goto(self.settings.board_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2000)
        print("브라우저가 열렸습니다. Cloudflare 통과와 ArcaLive 로그인을 완료한 뒤 Enter를 누르세요.")
        input()

    def fetch(self, url: str) -> str:
        page = self._require_page()
        self._emit_verbose(f"GET {url}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)
        except self._timeout_error as exc:
            raise CrawlBlockedError(f"Timed out while loading {url}") from exc
        html = page.content()
        self._emit_verbose(f"OK {url} ({len(html.encode('utf-8'))} bytes)")
        if "Just a moment..." in html or "Enable JavaScript and cookies to continue" in html:
            raise CrawlBlockedError(self._blocked_message())
        return html

    def close(self) -> None:
        if self._closed:
            return
        try:
            if self._close_page and self._page is not None:
                self._page.close()
        finally:
            try:
                if self._close_context and self._context is not None:
                    self._context.close()
            finally:
                try:
                    if self._close_browser and self._browser is not None:
                        self._browser.close()
                finally:
                    self._playwright_manager.stop()
                    self._closed = True

    def _start(self) -> None:
        if not self.force_persistent and self.settings.should_use_cdp:
            self._mode = "cdp"
            self._emit_verbose(f"browser mode=cdp endpoint={self.settings.playwright_cdp_url}")
            self._browser, self._context, self._page = self._connect_cdp_page()
            self._close_context = False
            self._close_page = True
        elif not self.force_persistent and self.settings.should_use_storage_state:
            self._mode = "storage-state"
            self._emit_verbose(
                f"browser mode=storage-state path={self.settings.playwright_storage_state_path}"
            )
            self._context, self._browser = self._launch_storage_state_context()
            self._close_browser = True
        else:
            self._mode = "profile"
            self._emit_verbose(
                f"browser mode=profile path={self.settings.playwright_profile_dir}"
            )
            self._context = self._launch_persistent_context()

        if self._page is None:
            self._page = self._get_primary_page(self._context)
        self._page.set_default_timeout(60000)

    def _launch_persistent_context(self) -> BrowserContext:
        return self._playwright.chromium.launch_persistent_context(
            str(self.settings.playwright_profile_dir),
            headless=self.headless,
        )

    def _launch_storage_state_context(self) -> tuple[BrowserContext, Browser]:
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
        browser = self._playwright.chromium.launch(headless=self.headless)
        context = browser.new_context(storage_state=str(storage_state_path))
        return context, browser

    def _connect_cdp_page(self) -> tuple[Browser, BrowserContext, Page]:
        cdp_url = self.settings.playwright_cdp_url
        if not cdp_url:
            raise BrowserSessionError("PLAYWRIGHT_CDP_URL is not configured.")
        browser = self._playwright.chromium.connect_over_cdp(cdp_url)
        if not browser.contexts:
            raise BrowserSessionError(
                "The connected browser has no available context. Open arca.live in the "
                "remote-debugging browser window, then retry."
            )
        context = browser.contexts[0]
        page = context.new_page()
        return browser, context, page

    def _get_primary_page(self, context: BrowserContext | None) -> Page:
        if context is None:
            raise BrowserSessionError("Browser context was not created.")
        if context.pages:
            return context.pages[0]
        return context.new_page()

    def _require_page(self) -> Page:
        if self._page is None:
            raise BrowserSessionError("Browser page was not created.")
        return self._page

    def _blocked_message(self) -> str:
        if self._mode == "cdp":
            return (
                "ArcaLive blocked the attached local browser session. In the "
                "remote-debugging browser window, open arca.live, complete any "
                "Cloudflare or login step there, and rerun the crawl/export command."
            )
        if self._mode == "storage-state":
            return (
                "ArcaLive blocked the current browser session. The imported "
                "state file is missing, expired, or incomplete. Export a fresh "
                "Playwright state or browser cookie JSON on an external machine "
                "and run `rag import-state <path>` again."
            )
        return "ArcaLive blocked the current browser session. Run `rag auth` to refresh the saved profile."

    def _emit_verbose(self, message: str) -> None:
        if not self.verbose:
            return
        print(f"[browser] {message}", file=sys.stderr, flush=True)


def create_transport(
    settings: Settings,
    transport_name: str,
    *,
    headless: bool = True,
    force_persistent: bool = False,
    verbose: bool = False,
) -> FetchTransport:
    if transport_name == "browser":
        return BrowserTransport(
            settings,
            headless=headless,
            force_persistent=force_persistent,
            verbose=verbose,
        )
    if transport_name == "custom-http":
        return CustomHTTPTransport(
            settings,
            headless=headless,
            force_persistent=force_persistent,
            verbose=verbose,
        )
    supported = ", ".join(SUPPORTED_TRANSPORTS)
    raise UnsupportedTransportError(
        f"Unsupported transport '{transport_name}'. Expected one of: {supported}."
    )
