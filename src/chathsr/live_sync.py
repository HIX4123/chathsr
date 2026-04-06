from __future__ import annotations

import json
import posixpath
import secrets
import shlex
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from chathsr.config import Settings
from chathsr.crawl import ArcaLiveCrawler
from chathsr.db import Database
from chathsr.errors import ChathsrError, RemoteSyncError
from chathsr.models import ParsedArticle
from chathsr.post_exports import export_articles_jsonl


SubprocessRunner = Callable[..., object]
CycleCallback = Callable[["LiveSyncCycleResult"], None]
ErrorCallback = Callable[[ChathsrError], None]


@dataclass(slots=True, frozen=True)
class RemoteSyncTarget:
    ssh_host: str
    ssh_user: str
    ssh_port: int
    remote_inbox_dir: str
    remote_rag_bin: str
    ssh_identity_file: Path | None = None

    @property
    def ssh_destination(self) -> str:
        return f"{self.ssh_user}@{self.ssh_host}"

    @property
    def remote_project_root(self) -> str:
        rag_path = PurePosixPath(self.remote_rag_bin)
        if not rag_path.is_absolute():
            raise RemoteSyncError(
                "The remote `rag` path must be absolute, for example `/srv/chathsr/.venv/bin/rag`."
            )
        try:
            return str(rag_path.parents[2])
        except IndexError as exc:
            raise RemoteSyncError(
                "The remote `rag` path must look like `/path/to/project/.venv/bin/rag`."
            ) from exc


@dataclass(slots=True, frozen=True)
class LiveSyncCycleResult:
    sync_stats: dict[str, int]
    pending_posts: int
    exported_posts: int
    marked_posts: int
    uploaded: bool
    remote_file: str | None


def resolve_remote_sync_target(
    settings: Settings,
    *,
    ssh_host: str | None = None,
    ssh_user: str | None = None,
    ssh_port: int | None = None,
    ssh_identity_file: Path | None = None,
    remote_inbox_dir: str | None = None,
    remote_rag_bin: str | None = None,
) -> RemoteSyncTarget:
    host = _clean_string(ssh_host) or settings.remote_sync_ssh_host
    user = _clean_string(ssh_user) or settings.remote_sync_ssh_user
    port = ssh_port or settings.remote_sync_ssh_port
    identity_file = ssh_identity_file or settings.remote_sync_ssh_identity_file
    inbox_dir = _clean_string(remote_inbox_dir) or settings.remote_sync_remote_inbox_dir
    rag_bin = _clean_string(remote_rag_bin) or settings.remote_sync_remote_rag_bin

    missing: list[str] = []
    if host is None:
        missing.append("REMOTE_SYNC_SSH_HOST / --ssh-host")
    if user is None:
        missing.append("REMOTE_SYNC_SSH_USER / --ssh-user")
    if inbox_dir is None:
        missing.append("REMOTE_SYNC_REMOTE_INBOX_DIR / --remote-inbox-dir")
    if rag_bin is None:
        missing.append("REMOTE_SYNC_REMOTE_RAG_BIN / --remote-rag-bin")
    if missing:
        raise RemoteSyncError(
            "Missing live-sync configuration: " + ", ".join(missing)
        )
    if port < 1:
        raise RemoteSyncError("SSH port must be a positive integer.")

    return RemoteSyncTarget(
        ssh_host=host,
        ssh_user=user,
        ssh_port=port,
        ssh_identity_file=identity_file,
        remote_inbox_dir=inbox_dir,
        remote_rag_bin=rag_bin,
    )


def run_live_sync(
    settings: Settings,
    db: Database,
    target: RemoteSyncTarget,
    *,
    max_pages: int | None = None,
    unchanged_limit: int = 20,
    interval_seconds: int | None = None,
    verbose: bool = False,
    once: bool = False,
    on_cycle_complete: CycleCallback | None = None,
    on_cycle_error: ErrorCallback | None = None,
    crawler_factory: Callable[[Settings], ArcaLiveCrawler] = ArcaLiveCrawler,
    command_runner: SubprocessRunner = subprocess.run,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    poll_interval = interval_seconds or settings.remote_sync_poll_interval_seconds
    if poll_interval < 1:
        raise RemoteSyncError("Live-sync interval must be at least 1 second.")

    cycles = 0
    while True:
        cycles += 1
        try:
            result = run_live_sync_cycle(
                settings,
                db,
                target,
                max_pages=max_pages,
                unchanged_limit=unchanged_limit,
                verbose=verbose,
                crawler_factory=crawler_factory,
                command_runner=command_runner,
            )
        except ChathsrError as exc:
            if once:
                raise
            if on_cycle_error is not None:
                on_cycle_error(exc)
        else:
            if on_cycle_complete is not None:
                on_cycle_complete(result)

        if once:
            return cycles

        _emit_verbose(
            verbose,
            f"sleep {poll_interval}s before the next live-sync cycle",
        )
        sleeper(poll_interval)


def run_live_sync_cycle(
    settings: Settings,
    db: Database,
    target: RemoteSyncTarget,
    *,
    max_pages: int | None = None,
    unchanged_limit: int = 20,
    verbose: bool = False,
    crawler_factory: Callable[[Settings], ArcaLiveCrawler] = ArcaLiveCrawler,
    command_runner: SubprocessRunner = subprocess.run,
) -> LiveSyncCycleResult:
    crawler = crawler_factory(settings)
    sync_stats = crawler.sync(
        db,
        max_pages=max_pages,
        unchanged_limit=unchanged_limit,
        verbose=verbose,
    )

    pending_rows = db.select_posts_pending_remote_sync()
    if not pending_rows:
        _emit_verbose(verbose, "no pending posts to push to the server")
        return LiveSyncCycleResult(
            sync_stats=sync_stats,
            pending_posts=0,
            exported_posts=0,
            marked_posts=0,
            uploaded=False,
            remote_file=None,
        )

    sync_pairs = [
        (int(row["post_id"]), str(row["content_hash"]))
        for row in pending_rows
    ]
    articles = [_row_to_article(row) for row in pending_rows]

    with tempfile.TemporaryDirectory(prefix="chathsr-live-sync-") as temp_dir:
        export_path = Path(temp_dir) / "pending-posts.jsonl"
        exported_posts = export_articles_jsonl(export_path, articles)
        remote_file = posixpath.join(
            target.remote_inbox_dir,
            _build_remote_filename(),
        )
        _emit_verbose(
            verbose,
            f"export {exported_posts} pending posts to {export_path}",
        )
        upload_and_import_posts(
            export_path,
            remote_file=remote_file,
            target=target,
            verbose=verbose,
            command_runner=command_runner,
        )

    marked_posts = db.mark_posts_remote_synced(sync_pairs)
    _emit_verbose(verbose, f"marked {marked_posts} posts as remotely synced")
    return LiveSyncCycleResult(
        sync_stats=sync_stats,
        pending_posts=len(pending_rows),
        exported_posts=exported_posts,
        marked_posts=marked_posts,
        uploaded=True,
        remote_file=remote_file,
    )


def upload_and_import_posts(
    local_path: str | Path,
    *,
    remote_file: str,
    target: RemoteSyncTarget,
    verbose: bool = False,
    command_runner: SubprocessRunner = subprocess.run,
) -> None:
    local_export_path = Path(local_path).resolve()
    _run_subprocess(
        _build_ssh_command(
            target,
            f"mkdir -p {shlex.quote(target.remote_inbox_dir)}",
        ),
        description="prepare remote inbox",
        verbose=verbose,
        command_runner=command_runner,
    )
    _run_subprocess(
        _build_scp_command(
            target,
            local_export_path=local_export_path,
            remote_file=remote_file,
        ),
        description="upload pending JSONL",
        verbose=verbose,
        command_runner=command_runner,
    )
    remote_command = " && ".join(
        [
            f"cd {shlex.quote(target.remote_project_root)}",
            f"{shlex.quote(target.remote_rag_bin)} import-posts {shlex.quote(remote_file)}",
            f"{shlex.quote(target.remote_rag_bin)} index changed-only",
            f"rm -f {shlex.quote(remote_file)}",
        ]
    )
    _run_subprocess(
        _build_ssh_command(target, remote_command),
        description="import and index uploaded posts on the server",
        verbose=verbose,
        command_runner=command_runner,
    )


def format_live_sync_cycle_result(result: LiveSyncCycleResult) -> str:
    sync_stats = result.sync_stats
    message = (
        "Live sync cycle complete: "
        f"pages={sync_stats['pages']} "
        f"articles={sync_stats['articles']} "
        f"new={sync_stats['new_posts']} "
        f"changed={sync_stats['changed_posts']} "
        f"failed={sync_stats['failed_articles']} "
        f"pending={result.pending_posts} "
        f"exported={result.exported_posts} "
        f"marked={result.marked_posts}"
    )
    if result.remote_file is not None:
        message += f" remote_file={result.remote_file}"
    return message


def _run_subprocess(
    command: list[str],
    *,
    description: str,
    verbose: bool,
    command_runner: SubprocessRunner,
) -> None:
    _emit_verbose(verbose, f"{description}: {_format_command(command)}")
    try:
        completed = command_runner(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RemoteSyncError(
            f"Failed to run {command[0]!r}. Make sure SSH tools are installed and on PATH."
        ) from exc
    except OSError as exc:
        raise RemoteSyncError(f"Failed to run {command[0]!r}: {exc}") from exc

    returncode = int(getattr(completed, "returncode", 0))
    if returncode == 0:
        return

    stdout = str(getattr(completed, "stdout", "")).strip()
    stderr = str(getattr(completed, "stderr", "")).strip()
    detail_parts = [f"{description} failed with exit code {returncode}."]
    if stderr:
        detail_parts.append(f"stderr: {stderr}")
    if stdout:
        detail_parts.append(f"stdout: {stdout}")
    raise RemoteSyncError(" ".join(detail_parts))


def _build_ssh_command(target: RemoteSyncTarget, remote_command: str) -> list[str]:
    return [
        "ssh",
        *_ssh_connection_args(target, scp=False),
        target.ssh_destination,
        "sh",
        "-lc",
        remote_command,
    ]


def _build_scp_command(
    target: RemoteSyncTarget,
    *,
    local_export_path: Path,
    remote_file: str,
) -> list[str]:
    return [
        "scp",
        *_ssh_connection_args(target, scp=True),
        str(local_export_path),
        f"{target.ssh_destination}:{remote_file}",
    ]


def _ssh_connection_args(target: RemoteSyncTarget, *, scp: bool) -> list[str]:
    args = ["-P" if scp else "-p", str(target.ssh_port)]
    if target.ssh_identity_file is not None:
        args.extend(["-i", str(target.ssh_identity_file)])
    return args


def _row_to_article(row) -> ParsedArticle:
    return ParsedArticle(
        post_id=int(row["post_id"]),
        url=str(row["url"]),
        title=str(row["title"]),
        category_label=_optional_string(row["category_label"]),
        created_at=_optional_string(row["created_at"]),
        author=_optional_string(row["author"]),
        body_text=str(row["body_text"] or ""),
        image_urls=_load_json_list(row["image_urls_json"]),
        video_urls=_load_json_list(row["video_urls_json"]),
        raw_html=str(row["raw_html"] or ""),
        content_hash=str(row["content_hash"]),
    )


def _load_json_list(value: object) -> list[str]:
    if value in (None, ""):
        return []
    parsed = json.loads(str(value))
    if not isinstance(parsed, list):
        raise RemoteSyncError("Post export data is corrupted: media URL fields must be lists.")
    return [str(item) for item in parsed]


def _build_remote_filename() -> str:
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(4)
    return f"live-sync-{timestamp}-{suffix}.jsonl"


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _clean_string(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _format_command(command: Iterable[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def _emit_verbose(verbose: bool, message: str) -> None:
    if not verbose:
        return
    print(f"[live-sync] {message}", file=sys.stderr, flush=True)
