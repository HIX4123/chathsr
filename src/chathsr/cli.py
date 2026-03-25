from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path

import typer

from chathsr.config import load_settings
from chathsr.crawl import ArcaLiveCrawler
from chathsr.db import Database
from chathsr.errors import ChathsrError
from chathsr.gemini_client import GeminiClient
from chathsr.indexing import index_posts
from chathsr.post_exports import export_articles_jsonl, import_articles_jsonl
from chathsr.retrieval import answer_question
from chathsr.session_state import import_storage_state_file
from chathsr.transports import DEFAULT_TRANSPORT, SUPPORTED_TRANSPORTS
from chathsr.websocket_probe import run_websocket_probe, summarize_probe_file


app = typer.Typer(no_args_is_help=True)
crawl_app = typer.Typer(no_args_is_help=True)
index_app = typer.Typer(no_args_is_help=True)
probe_app = typer.Typer(no_args_is_help=True)
app.add_typer(crawl_app, name="crawl")
app.add_typer(index_app, name="index")
app.add_typer(probe_app, name="probe")
TRANSPORT_HELP = f"Crawl transport to use: {', '.join(SUPPORTED_TRANSPORTS)}."


def _fail(exc: ChathsrError) -> typer.Exit:
    typer.echo(str(exc), err=True)
    return typer.Exit(code=1)


@contextmanager
def command_context(command: str, detail: str = ""):
    settings = load_settings()
    db = Database(settings.database_path)
    run_id = db.record_run_start(command, detail=detail)
    try:
        yield settings, db
        db.record_run_finish(run_id, status="succeeded", detail=detail)
    except Exception as exc:
        db.record_run_finish(run_id, status="failed", detail=str(exc))
        raise
    finally:
        db.close()


@app.command()
def auth() -> None:
    """Open a persistent browser profile for Cloudflare/login setup."""
    try:
        with command_context("auth") as (settings, _db):
            crawler = ArcaLiveCrawler(settings)
            crawler.authenticate()
            typer.echo(f"Saved browser profile under {settings.playwright_profile_dir}")
    except ChathsrError as exc:
        raise _fail(exc)


@app.command("import-state")
def import_state(
    path: Path = typer.Argument(..., help="Path to a Playwright storage_state.json file."),
) -> None:
    """Import a Playwright storage_state.json or browser cookie JSON file."""
    try:
        with command_context("import-state", detail=str(path)) as (settings, _db):
            destination, detected_format = import_storage_state_file(
                path,
                settings.playwright_storage_state_path,
            )
            if detected_format == "storage_state":
                typer.echo(f"Imported Playwright storage state to {destination}")
            else:
                typer.echo(
                    f"Imported browser cookie JSON and converted it to Playwright storage state at {destination}"
                )
            typer.echo("Next step: python -m chathsr crawl backfill --max-pages 1")
    except ChathsrError as exc:
        raise _fail(exc)


@app.command("import-posts")
def import_posts(
    path: Path = typer.Argument(..., help="Path to a JSONL export file or directory."),
) -> None:
    """Import locally exported post JSONL files into SQLite."""
    try:
        with command_context("import-posts", detail=str(path)) as (_settings, db):
            stats = import_articles_jsonl(path, db)
            typer.echo(
                f"Imported posts: files={stats['files']} articles={stats['articles']} "
                f"new={stats['new_posts']} changed={stats['changed_posts']}"
            )
            typer.echo("Next step: python -m chathsr index changed-only")
    except ChathsrError as exc:
        raise _fail(exc)


@crawl_app.command("backfill")
def crawl_backfill(
    max_pages: int | None = typer.Option(None, help="Optional page limit for the initial backfill."),
    headless: bool = typer.Option(True, "--headless/--headful", help="Use the saved browser profile headlessly or with a visible browser."),
    transport: str = typer.Option(DEFAULT_TRANSPORT, "--transport", help=TRANSPORT_HELP),
    verbose: bool = typer.Option(False, "--verbose", help="Print crawl progress and requested URLs."),
) -> None:
    """Crawl the full 정보 category history into SQLite."""
    try:
        with command_context("crawl backfill", detail=f"max_pages={max_pages}") as (
            settings,
            db,
        ):
            crawler = ArcaLiveCrawler(settings)
            stats = crawler.crawl_backfill(
                db,
                max_pages=max_pages,
                headless=headless,
                transport_name=transport,
                verbose=verbose,
            )
            typer.echo(
                f"Backfill complete: pages={stats['pages']} articles={stats['articles']} "
                f"new={stats['new_posts']} changed={stats['changed_posts']}"
            )
    except ChathsrError as exc:
        raise _fail(exc)


@crawl_app.command("export-jsonl")
def crawl_export_jsonl(
    output: Path = typer.Argument(..., help="Destination JSONL file path."),
    max_pages: int | None = typer.Option(None, help="Optional page limit for the export."),
    headless: bool = typer.Option(True, "--headless/--headful", help="Use the active browser session headlessly or with a visible browser."),
    transport: str = typer.Option(DEFAULT_TRANSPORT, "--transport", help=TRANSPORT_HELP),
    verbose: bool = typer.Option(False, "--verbose", help="Print crawl progress and requested URLs."),
) -> None:
    """Crawl info posts and export them as JSONL for later import."""
    try:
        with command_context("crawl export-jsonl", detail=str(output)) as (
            settings,
            db,
        ):
            crawler = ArcaLiveCrawler(settings)
            articles, stats = crawler.crawl_backfill_articles(
                max_pages=max_pages,
                headless=headless,
                transport_name=transport,
                verbose=verbose,
            )
            count = export_articles_jsonl(output, articles)
            db.set_crawl_state("last_export_jsonl_at", str(output.resolve()))
            typer.echo(
                f"Export complete: pages={stats['pages']} articles={count} output={output.resolve()}"
            )
    except ChathsrError as exc:
        raise _fail(exc)


@probe_app.command("websocket")
def probe_websocket(
    cdp_url: str = typer.Option(..., "--cdp-url", help="HTTP or WS URL for a remote-debugging Chrome/Edge instance."),
    duration: int = typer.Option(60, min=1, help="How many seconds to observe websocket traffic."),
    output: Path = typer.Option(..., "--output", help="JSONL file that will receive websocket probe records."),
) -> None:
    """Capture websocket-related CDP events from a remote-debugging browser."""
    try:
        with command_context("probe websocket", detail=f"cdp_url={cdp_url}") as (_settings, _db):
            stats = asyncio.run(
                run_websocket_probe(
                    cdp_url=cdp_url,
                    duration=duration,
                    output_path=output,
                )
            )
            typer.echo(
                f"Probe complete: records={stats['records']} "
                f"connections={stats['connections']} output={output.resolve()}"
            )
    except ChathsrError as exc:
        raise _fail(exc)


@probe_app.command("summarize")
def probe_summarize(
    path: Path = typer.Argument(..., help="Path to a websocket probe JSONL file."),
) -> None:
    """Summarize a previously captured websocket probe log."""
    try:
        with command_context("probe summarize", detail=str(path)) as (_settings, _db):
            typer.echo(summarize_probe_file(path))
    except ChathsrError as exc:
        raise _fail(exc)


@app.command()
def sync(
    max_pages: int | None = typer.Option(None, help="Optional page limit for incremental sync."),
    headless: bool = typer.Option(True, "--headless/--headful", help="Use the saved browser profile headlessly or with a visible browser."),
    unchanged_limit: int = typer.Option(20, min=1, help="Stop after this many unchanged posts in a row."),
    transport: str = typer.Option(DEFAULT_TRANSPORT, "--transport", help=TRANSPORT_HELP),
    verbose: bool = typer.Option(False, "--verbose", help="Print crawl progress and requested URLs."),
) -> None:
    """Sync newly added or recently edited info posts."""
    try:
        with command_context("sync", detail=f"max_pages={max_pages}") as (settings, db):
            crawler = ArcaLiveCrawler(settings)
            stats = crawler.sync(
                db,
                max_pages=max_pages,
                headless=headless,
                unchanged_limit=unchanged_limit,
                transport_name=transport,
                verbose=verbose,
            )
            typer.echo(
                f"Sync complete: pages={stats['pages']} articles={stats['articles']} "
                f"new={stats['new_posts']} changed={stats['changed_posts']}"
            )
    except ChathsrError as exc:
        raise _fail(exc)


@index_app.command("changed-only")
def index_changed_only() -> None:
    """Embed only new or changed posts."""
    try:
        with command_context("index changed-only") as (settings, db):
            gemini = GeminiClient(settings)
            count = index_posts(
                db,
                settings,
                gemini,
                changed_only=True,
                full_reembed=False,
            )
            typer.echo(f"Indexed {count} changed posts. Total chunks: {db.count_chunks()}")
    except ChathsrError as exc:
        raise _fail(exc)


@index_app.command("full-reembed")
def index_full_reembed() -> None:
    """Delete and rebuild all embeddings using the current embedding model."""
    try:
        with command_context("index full-reembed") as (settings, db):
            gemini = GeminiClient(settings)
            count = index_posts(
                db,
                settings,
                gemini,
                changed_only=False,
                full_reembed=True,
            )
            typer.echo(f"Rebuilt embeddings for {count} posts. Total chunks: {db.count_chunks()}")
    except ChathsrError as exc:
        raise _fail(exc)


@app.command()
def ask(
    question: str = typer.Argument(..., help="Question to ask the local RAG store."),
    top_k: int | None = typer.Option(None, min=1, help="Number of retrieved chunks to feed into Gemini."),
    cheap: bool = typer.Option(False, help="Use the cheaper fallback generation model."),
) -> None:
    """Answer a question using retrieved info posts plus Gemini generation."""
    try:
        with command_context("ask", detail=question) as (settings, db):
            gemini = GeminiClient(settings)
            answer = answer_question(
                db,
                settings,
                gemini,
                question=question,
                top_k=top_k,
                use_cheap_model=cheap,
            )
            typer.echo(answer)
    except ChathsrError as exc:
        raise _fail(exc)


@app.command()
def refresh(
    max_pages: int | None = typer.Option(None, help="Optional page limit for the incremental sync step."),
    headless: bool = typer.Option(True, "--headless/--headful", help="Use the saved browser profile headlessly or with a visible browser."),
    transport: str = typer.Option(DEFAULT_TRANSPORT, "--transport", help=TRANSPORT_HELP),
    verbose: bool = typer.Option(False, "--verbose", help="Print crawl progress and requested URLs."),
) -> None:
    """Run sync, then embed only new or changed posts."""
    try:
        with command_context("refresh", detail=f"max_pages={max_pages}") as (settings, db):
            crawler = ArcaLiveCrawler(settings)
            sync_stats = crawler.sync(
                db,
                max_pages=max_pages,
                headless=headless,
                transport_name=transport,
                verbose=verbose,
            )
            gemini = GeminiClient(settings)
            indexed = index_posts(
                db,
                settings,
                gemini,
                changed_only=True,
                full_reembed=False,
            )
            typer.echo(
                f"Refresh complete: pages={sync_stats['pages']} articles={sync_stats['articles']} "
                f"new={sync_stats['new_posts']} changed={sync_stats['changed_posts']} indexed={indexed}"
            )
    except ChathsrError as exc:
        raise _fail(exc)
