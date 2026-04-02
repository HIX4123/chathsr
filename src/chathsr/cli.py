from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path

import typer

from chathsr.config import load_settings
from chathsr.crawl import ArcaLiveCrawler
from chathsr.db import Database
from chathsr.errors import ChathsrError, SyncBatchError
from chathsr.gemini_client import GeminiClient
from chathsr.http_transport import (
    HTTPProbeResult,
    list_probe_profile_names,
    load_probe_cookie_jar,
    run_http_probe_matrix,
)
from chathsr.indexing import index_posts
from chathsr.models import ParsedArticle
from chathsr.post_exports import export_articles_jsonl, import_articles_jsonl
from chathsr.retrieval import answer_question
from chathsr.sync_batches import (
    archive_sync_batch,
    count_jsonl_articles,
    create_sync_batch,
    find_sync_batch,
    list_sync_batches,
    load_sync_batch_metadata,
    push_sync_batch,
    read_remote_sync_status,
    SyncStatus,
    SyncStatusPost,
)


app = typer.Typer(no_args_is_help=True)
crawl_app = typer.Typer(no_args_is_help=True)
index_app = typer.Typer(no_args_is_help=True)
probe_app = typer.Typer(no_args_is_help=True)
sync_app = typer.Typer(no_args_is_help=False, invoke_without_command=True)
app.add_typer(crawl_app, name="crawl")
app.add_typer(index_app, name="index")
app.add_typer(probe_app, name="probe")
app.add_typer(sync_app, name="sync")


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


@app.command("import-posts")
def import_posts(
    path: Path = typer.Argument(..., help="Path to a JSONL export file or directory."),
    verbose: bool = typer.Option(False, "--verbose", help="Print detailed progress logs."),
) -> None:
    """Import locally exported post JSONL files into SQLite."""
    try:
        with command_context("import-posts", detail=str(path)) as (_settings, db):
            stats = import_articles_jsonl(path, db, verbose=verbose)
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
                verbose=verbose,
            )
            count = export_articles_jsonl(output, articles)
            db.set_crawl_state("last_export_jsonl_at", str(output.resolve()))
            typer.echo(
                f"Export complete: pages={stats['pages']} articles={count} output={output.resolve()}"
            )
    except ChathsrError as exc:
        raise _fail(exc)


@crawl_app.command("export-sync-batch")
def crawl_export_sync_batch(
    output_dir: Path | None = typer.Argument(
        None,
        help="Destination directory for the sync batch. Defaults to SYNC_CLIENT_OUTBOX_DIR.",
    ),
    since_post_id: int | None = typer.Option(
        None,
        "--since-post-id",
        min=1,
        help="Only export posts with post_id greater than this value.",
    ),
    auto_since_server: bool = typer.Option(
        False,
        "--auto-since-server",
        help="Query the configured server and export only posts newer than its latest post_id.",
    ),
    recheck_posts: int | None = typer.Option(
        None,
        "--recheck-posts",
        min=0,
        help="Re-fetch this many of the newest posts to detect edits. Defaults to 20 with --auto-since-server, otherwise 0.",
    ),
    max_pages: int | None = typer.Option(None, help="Optional page limit for the export."),
    verbose: bool = typer.Option(False, "--verbose", help="Print crawl progress and requested URLs."),
) -> None:
    """Crawl info posts and export them as a sync batch pair."""
    if since_post_id is not None and auto_since_server:
        raise typer.BadParameter("Choose either --since-post-id or --auto-since-server, not both.")
    try:
        with command_context(
            "crawl export-sync-batch",
            detail=str(output_dir or "<default>"),
        ) as (settings, db):
            crawler = ArcaLiveCrawler(settings)
            resolved_recheck_posts = _resolve_recheck_posts(
                auto_since_server=auto_since_server,
                recheck_posts=recheck_posts,
            )
            sync_status = _resolve_remote_sync_status(
                settings,
                auto_since_server=auto_since_server,
                recheck_posts=resolved_recheck_posts,
                verbose=verbose,
            )
            resolved_since_post_id = (
                sync_status.latest_post_id if auto_since_server and sync_status is not None else since_post_id
            )
            articles, stats = crawler.crawl_incremental_articles(
                since_post_id=resolved_since_post_id,
                recheck_posts=resolved_recheck_posts,
                max_pages=max_pages,
                verbose=verbose,
            )
            articles = _filter_incremental_articles(
                articles,
                since_post_id=resolved_since_post_id,
                sync_status=sync_status,
            )
            if not articles:
                typer.echo(
                    "No new or changed posts to export"
                    + (
                        f" since post_id={resolved_since_post_id}"
                        if resolved_since_post_id is not None
                        else "."
                    )
                )
                return
            result = create_sync_batch(
                output_dir or settings.sync_client_outbox_dir,
                articles,
                settings=settings,
                since_post_id=resolved_since_post_id,
                recheck_posts=resolved_recheck_posts,
                max_pages=max_pages,
            )
            db.set_crawl_state("last_export_sync_batch_id", result.batch.batch_id)
            typer.echo(
                "Sync batch export complete: "
                f"pages={stats['pages']} articles={result.metadata.article_count} "
                f"since_post_id={result.metadata.since_post_id} min_post_id={result.metadata.min_post_id} "
                f"max_post_id={result.metadata.max_post_id} recheck_posts={result.metadata.recheck_posts} "
                f"batch={result.batch.batch_id} jsonl={result.batch.jsonl_path} "
                f"metadata={result.batch.metadata_path}"
            )
    except ChathsrError as exc:
        raise _fail(exc)


@probe_app.command("http")
def probe_http(
    url: str | None = typer.Option(
        None,
        "--url",
        help="URL to probe. Defaults to the board URL from settings.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        help="Optional JSON file path for the full diagnostics matrix.",
    ),
    proxy: str | None = typer.Option(
        None,
        "--proxy",
        help="Optional HTTP(S) proxy URL. If omitted, HTTPS_PROXY or HTTP_PROXY is used when set.",
    ),
    cookie_header: str | None = typer.Option(
        None,
        "--cookie-header",
        help="Optional raw Cookie header value for probe-only experiments.",
    ),
    cookie_json: Path | None = typer.Option(
        None,
        "--cookie-json",
        help="Optional JSON cookie file for probe-only experiments.",
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        help="Optional built-in request profile to run instead of the full matrix.",
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Print detailed probe logs."),
) -> None:
    """Run the HTTP probe profile matrix without touching crawl state."""
    if cookie_header and cookie_json is not None:
        raise typer.BadParameter("Choose either --cookie-header or --cookie-json, not both.")
    if profile is not None and profile not in list_probe_profile_names():
        available = ", ".join(list_probe_profile_names())
        raise typer.BadParameter(f"Unknown profile '{profile}'. Available: {available}")
    try:
        settings = load_settings()
        target_url = url or settings.board_url
        cookie_jar = load_probe_cookie_jar(cookie_json) if cookie_json is not None else None
        proxy_url = _resolve_probe_proxy(proxy)
        results = run_http_probe_matrix(
            settings,
            target_url,
            verbose=verbose,
            proxy_url=proxy_url,
            cookie_header=cookie_header,
            cookie_jar=cookie_jar,
            profile_name=profile,
        )

        typer.echo(f"HTTP probe target={target_url}")
        for result in results:
            typer.echo(_format_probe_result(result))
        typer.echo(_summarize_probe_results(results))
        if output is not None:
            output_path = output.resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(
                    [result.to_payload() for result in results],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            typer.echo(f"Saved probe output to {output_path}")
    except ChathsrError as exc:
        raise _fail(exc)


@sync_app.callback()
def sync(
    ctx: typer.Context,
    max_pages: int | None = typer.Option(None, help="Optional page limit for incremental sync."),
    unchanged_limit: int = typer.Option(20, min=1, help="Stop after this many unchanged posts in a row."),
    verbose: bool = typer.Option(False, "--verbose", help="Print crawl progress and requested URLs."),
) -> None:
    """Sync board posts or manage sync-batch transfers."""
    if ctx.invoked_subcommand is not None:
        return
    try:
        with command_context("sync", detail=f"max_pages={max_pages}") as (settings, db):
            stats = _run_board_sync(
                settings,
                db,
                max_pages=max_pages,
                unchanged_limit=unchanged_limit,
                verbose=verbose,
            )
            typer.echo(
                f"Sync complete: pages={stats['pages']} articles={stats['articles']} "
                f"new={stats['new_posts']} changed={stats['changed_posts']}"
            )
    except ChathsrError as exc:
        raise _fail(exc)


@sync_app.command("push-latest")
def sync_push_latest(
    batch_id: str | None = typer.Option(None, "--batch-id", help="Specific sync batch ID to upload."),
    verbose: bool = typer.Option(False, "--verbose", help="Print detailed sync transport logs."),
) -> None:
    """Upload the newest local sync batch to the configured server inbox."""
    try:
        with command_context("sync push-latest", detail=batch_id or "<latest>") as (settings, db):
            batch = find_sync_batch(settings.sync_client_outbox_dir, batch_id=batch_id)
            load_sync_batch_metadata(batch)
            push_sync_batch(batch, settings, verbose=verbose)
            db.set_crawl_state("last_pushed_sync_batch_id", batch.batch_id)
            typer.echo(
                f"Pushed sync batch: batch={batch.batch_id} "
                f"target={settings.sync_remote_user}@{settings.sync_remote_host}:{settings.sync_remote_path}"
            )
    except ChathsrError as exc:
        raise _fail(exc)


@sync_app.command("inbox")
def sync_inbox(
    verbose: bool = typer.Option(False, "--verbose", help="Print detailed import and indexing logs."),
) -> None:
    """Import and index sync batches uploaded into the server inbox."""
    try:
        with command_context("sync inbox", detail="inbox") as (settings, db):
            stats = _sync_inbox_batches(settings, db, verbose=verbose)
            typer.echo(
                "Inbox sync complete: "
                f"batches={stats['batches']} processed={stats['processed_batches']} "
                f"skipped={stats['skipped_batches']} failed={stats['failed_batches']} "
                f"articles={stats['articles']} new={stats['new_posts']} "
                f"changed={stats['changed_posts']} indexed={stats['indexed']}"
            )
            if stats["failed_batches"]:
                raise SyncBatchError(
                    f"Inbox sync completed with {stats['failed_batches']} failed batch(es)"
                )
    except ChathsrError as exc:
        raise _fail(exc)


@sync_app.command("status")
def sync_status(
    as_json: bool = typer.Option(False, "--json", help="Print the sync status as JSON."),
    recent_posts: int = typer.Option(
        0,
        "--recent-posts",
        min=0,
        help="Include up to this many recent post hashes in the JSON output.",
    ),
) -> None:
    """Print the server-side sync cursor used by incremental client exports."""
    try:
        with command_context("sync status", detail="status") as (_settings, db):
            status = _get_sync_status(db, recent_posts=recent_posts)
            if as_json:
                typer.echo(json.dumps(status.to_payload(), ensure_ascii=False))
            else:
                typer.echo(
                    "Sync status: "
                    f"latest_post_id={status.latest_post_id or 'none'} "
                    f"latest_crawled_at={status.latest_crawled_at or 'none'} "
                    f"latest_batch_id={status.latest_batch_id or 'none'} "
                    f"recent_posts={len(status.recent_posts)}"
                )
    except ChathsrError as exc:
        raise _fail(exc)


@index_app.command("changed-only")
def index_changed_only(
    verbose: bool = typer.Option(False, "--verbose", help="Print detailed progress logs."),
) -> None:
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
                verbose=verbose,
            )
            typer.echo(f"Indexed {count} changed posts. Total chunks: {db.count_chunks()}")
    except ChathsrError as exc:
        raise _fail(exc)


@index_app.command("full-reembed")
def index_full_reembed(
    verbose: bool = typer.Option(False, "--verbose", help="Print detailed progress logs."),
) -> None:
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
                verbose=verbose,
            )
            typer.echo(f"Rebuilt embeddings for {count} posts. Total chunks: {db.count_chunks()}")
    except ChathsrError as exc:
        raise _fail(exc)


@app.command()
def ask(
    question: str = typer.Argument(..., help="Question to ask the local RAG store."),
    top_k: int | None = typer.Option(None, min=1, help="Number of retrieved chunks to feed into Gemini."),
    cheap: bool = typer.Option(False, help="Use the cheaper fallback generation model."),
    verbose: bool = typer.Option(False, "--verbose", help="Print detailed progress logs."),
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
                verbose=verbose,
            )
            typer.echo(answer)
    except ChathsrError as exc:
        raise _fail(exc)


@app.command()
def refresh(
    max_pages: int | None = typer.Option(None, help="Optional page limit for the incremental sync step."),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="Print sync progress, requested URLs, and indexing logs.",
    ),
) -> None:
    """Run sync, then embed only new or changed posts."""
    try:
        with command_context("refresh", detail=f"max_pages={max_pages}") as (settings, db):
            sync_stats = _run_board_sync(
                settings,
                db,
                max_pages=max_pages,
                unchanged_limit=20,
                verbose=verbose,
            )
            indexed = _index_changed_posts(settings, db, verbose=verbose)
            typer.echo(
                f"Refresh complete: pages={sync_stats['pages']} articles={sync_stats['articles']} "
                f"new={sync_stats['new_posts']} changed={sync_stats['changed_posts']} indexed={indexed}"
            )
    except ChathsrError as exc:
        raise _fail(exc)


def _run_board_sync(
    settings,
    db,
    *,
    max_pages: int | None,
    unchanged_limit: int,
    verbose: bool,
) -> dict[str, int]:
    crawler = ArcaLiveCrawler(settings)
    return crawler.sync(
        db,
        max_pages=max_pages,
        unchanged_limit=unchanged_limit,
        verbose=verbose,
    )


def _index_changed_posts(settings, db, *, verbose: bool) -> int:
    gemini = GeminiClient(settings)
    return index_posts(
        db,
        settings,
        gemini,
        changed_only=True,
        full_reembed=False,
        verbose=verbose,
    )


def _resolve_recheck_posts(*, auto_since_server: bool, recheck_posts: int | None) -> int:
    if recheck_posts is not None:
        return recheck_posts
    return 20 if auto_since_server else 0


def _resolve_remote_sync_status(
    settings,
    *,
    auto_since_server: bool,
    recheck_posts: int,
    verbose: bool,
) -> SyncStatus | None:
    if not auto_since_server and recheck_posts <= 0:
        return None
    return read_remote_sync_status(
        settings,
        recent_posts=recheck_posts,
        verbose=verbose,
    )


def _filter_incremental_articles(
    articles: list[ParsedArticle],
    *,
    since_post_id: int | None,
    sync_status: SyncStatus | None,
) -> list[ParsedArticle]:
    if sync_status is None:
        return articles
    known_hashes = {post.post_id: post.content_hash for post in sync_status.recent_posts}
    filtered: list[ParsedArticle] = []
    for article in articles:
        if since_post_id is None or article.post_id > since_post_id:
            filtered.append(article)
            continue
        if known_hashes.get(article.post_id) != article.content_hash:
            filtered.append(article)
    return filtered


def _get_sync_status(db, *, recent_posts: int = 0) -> SyncStatus:
    latest_post = db.get_latest_post_summary()
    return SyncStatus(
        latest_post_id=int(latest_post["post_id"]) if latest_post else None,
        latest_crawled_at=latest_post["crawled_at"] if latest_post else None,
        latest_batch_id=db.get_latest_successful_sync_batch_id(),
        recent_posts=[
            SyncStatusPost(post_id=int(row["post_id"]), content_hash=str(row["content_hash"]))
            for row in db.list_recent_posts(limit=recent_posts)
        ],
    )


def _sync_inbox_batches(settings, db, *, verbose: bool) -> dict[str, int]:
    stats = {
        "batches": 0,
        "processed_batches": 0,
        "skipped_batches": 0,
        "failed_batches": 0,
        "articles": 0,
        "new_posts": 0,
        "changed_posts": 0,
        "indexed": 0,
    }
    for batch in list_sync_batches(settings.sync_inbox_dir):
        stats["batches"] += 1
        source_name = batch.jsonl_path.name
        batch_status = db.get_sync_batch_status(batch.batch_id)
        if batch_status == "succeeded":
            archive_sync_batch(batch, settings.sync_archive_dir, status="processed")
            stats["skipped_batches"] += 1
            continue
        try:
            metadata = load_sync_batch_metadata(batch)
            import_stats = import_articles_jsonl(batch.jsonl_path, db, verbose=verbose)
            indexed = _index_changed_posts(settings, db, verbose=verbose)
            archive_sync_batch(batch, settings.sync_archive_dir, status="processed")
            db.record_sync_batch(
                batch_id=batch.batch_id,
                source_name=source_name,
                status="succeeded",
                article_count=metadata.article_count,
            )
            stats["processed_batches"] += 1
            stats["articles"] += import_stats["articles"]
            stats["new_posts"] += import_stats["new_posts"]
            stats["changed_posts"] += import_stats["changed_posts"]
            stats["indexed"] += indexed
        except ChathsrError as exc:
            article_count = 0
            try:
                article_count = count_jsonl_articles(batch.jsonl_path)
            except OSError:
                pass
            archive_sync_batch(batch, settings.sync_archive_dir, status="failed")
            db.record_sync_batch(
                batch_id=batch.batch_id,
                source_name=source_name,
                status="failed",
                article_count=article_count,
                error_detail=str(exc),
            )
            stats["failed_batches"] += 1
    return stats


def _format_probe_result(result: HTTPProbeResult) -> str:
    status = result.status_code if result.status_code is not None else "n/a"
    blocked = "yes" if result.blocked else "no"
    lines = [
        (
            f"{result.profile} [{result.proxy_label or 'runtime-default'}/{result.cookie_mode}]: "
            f"status={status} blocked={blocked} redirects={result.redirect_count} "
            f"error={result.error_kind or 'none'} server={result.server or 'n/a'} "
            f"cf-ray={result.cf_ray or 'n/a'}"
        ),
        f"  ua={result.user_agent or 'n/a'}",
    ]
    if result.body_snippet:
        lines.append(f"  snippet={result.body_snippet}")
    if result.error_detail:
        lines.append(f"  detail={result.error_detail}")
    return "\n".join(lines)


def _resolve_probe_proxy(proxy: str | None) -> str | None:
    if proxy:
        return proxy
    return os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")


def _summarize_probe_results(results: list[HTTPProbeResult]) -> str:
    for result in results:
        if (
            not result.error_kind
            and not result.blocked
            and result.status_code is not None
            and 200 <= result.status_code < 300
        ):
            return (
                "Working combination: "
                f"profile={result.profile} proxy={result.proxy_label or 'runtime-default'} "
                f"cookies={result.cookie_mode} status={result.status_code}"
            )

    counts: dict[str, int] = {}
    for result in results:
        key = result.error_kind or "success"
        counts[key] = counts.get(key, 0) + 1
    summary = ", ".join(f"{key}={counts[key]}" for key in sorted(counts))
    return f"No working combination found. Result summary: {summary}"
