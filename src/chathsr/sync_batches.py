from __future__ import annotations

import json
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from chathsr.config import Settings
from chathsr.errors import SyncBatchError, SyncConfigurationError, SyncTransportError
from chathsr.models import ParsedArticle
from chathsr.post_exports import export_articles_jsonl
from chathsr.utils import utc_now_iso


SYNC_BATCH_GENERATOR = "chathsr-sync-v1"


@dataclass(slots=True)
class SyncBatchMetadata:
    batch_id: str
    created_at: str
    source_host: str
    board_slug: str
    category_label: str
    article_count: int
    since_post_id: int | None
    min_post_id: int | None
    max_post_id: int | None
    recheck_posts: int
    max_pages: int | None
    generator: str = SYNC_BATCH_GENERATOR

    def to_payload(self) -> dict[str, object]:
        return {
            "batch_id": self.batch_id,
            "created_at": self.created_at,
            "source_host": self.source_host,
            "board_slug": self.board_slug,
            "category_label": self.category_label,
            "article_count": self.article_count,
            "since_post_id": self.since_post_id,
            "min_post_id": self.min_post_id,
            "max_post_id": self.max_post_id,
            "recheck_posts": self.recheck_posts,
            "max_pages": self.max_pages,
            "generator": self.generator,
        }

    @classmethod
    def from_payload(
        cls,
        payload: object,
        *,
        expected_batch_id: str,
        source_path: Path,
    ) -> "SyncBatchMetadata":
        if not isinstance(payload, dict):
            raise SyncBatchError(f"{source_path} must contain a JSON object")
        batch_id = _required_string(payload, "batch_id", source_path)
        if batch_id != expected_batch_id:
            raise SyncBatchError(
                f"{source_path} batch_id mismatch: expected {expected_batch_id}, got {batch_id}"
            )
        created_at = _required_string(payload, "created_at", source_path)
        source_host = _required_string(payload, "source_host", source_path)
        board_slug = _required_string(payload, "board_slug", source_path)
        category_label = _required_string(payload, "category_label", source_path)
        generator = _required_string(payload, "generator", source_path)
        if generator != SYNC_BATCH_GENERATOR:
            raise SyncBatchError(
                f"{source_path} generator must be {SYNC_BATCH_GENERATOR!r}, got {generator!r}"
            )
        article_count = payload.get("article_count")
        if not isinstance(article_count, int) or article_count < 0:
            raise SyncBatchError(f"{source_path} article_count must be a non-negative integer")
        since_post_id = _optional_non_negative_int(payload.get("since_post_id"), "since_post_id", source_path)
        min_post_id = _optional_non_negative_int(payload.get("min_post_id"), "min_post_id", source_path)
        max_post_id = _optional_non_negative_int(payload.get("max_post_id"), "max_post_id", source_path)
        recheck_posts = _optional_non_negative_int(payload.get("recheck_posts"), "recheck_posts", source_path)
        max_pages = payload.get("max_pages")
        if max_pages is not None and (not isinstance(max_pages, int) or max_pages < 1):
            raise SyncBatchError(f"{source_path} max_pages must be null or an integer >= 1")
        return cls(
            batch_id=batch_id,
            created_at=created_at,
            source_host=source_host,
            board_slug=board_slug,
            category_label=category_label,
            article_count=article_count,
            since_post_id=since_post_id,
            min_post_id=min_post_id,
            max_post_id=max_post_id,
            recheck_posts=recheck_posts or 0,
            max_pages=max_pages,
            generator=generator,
        )


@dataclass(slots=True)
class SyncBatchFiles:
    batch_id: str
    jsonl_path: Path
    metadata_path: Path


@dataclass(slots=True)
class SyncBatchExportResult:
    batch: SyncBatchFiles
    metadata: SyncBatchMetadata


@dataclass(slots=True)
class SyncStatusPost:
    post_id: int
    content_hash: str

    def to_payload(self) -> dict[str, object]:
        return {
            "post_id": self.post_id,
            "content_hash": self.content_hash,
        }


@dataclass(slots=True)
class SyncStatus:
    latest_post_id: int | None
    latest_crawled_at: str | None
    latest_batch_id: str | None
    recent_posts: list[SyncStatusPost]

    def to_payload(self) -> dict[str, object]:
        return {
            "latest_post_id": self.latest_post_id,
            "latest_crawled_at": self.latest_crawled_at,
            "latest_batch_id": self.latest_batch_id,
            "recent_posts": [post.to_payload() for post in self.recent_posts],
        }

    @classmethod
    def from_payload(cls, payload: object, *, source: str) -> "SyncStatus":
        if not isinstance(payload, dict):
            raise SyncTransportError(f"{source} must return a JSON object")
        try:
            latest_post_id = _optional_non_negative_int(
                payload.get("latest_post_id"),
                "latest_post_id",
                source,
            )
            latest_crawled_at = _optional_string(payload.get("latest_crawled_at"))
            latest_batch_id = _optional_string(payload.get("latest_batch_id"))
            recent_posts = _parse_recent_posts(payload.get("recent_posts"), source)
            return cls(
                latest_post_id=latest_post_id,
                latest_crawled_at=latest_crawled_at,
                latest_batch_id=latest_batch_id,
                recent_posts=recent_posts,
            )
        except SyncBatchError as exc:
            raise SyncTransportError(str(exc)) from exc


def create_sync_batch(
    output_dir: str | Path,
    articles: Iterable[ParsedArticle],
    *,
    settings: Settings,
    since_post_id: int | None,
    recheck_posts: int,
    max_pages: int | None,
) -> SyncBatchExportResult:
    batch_dir = Path(output_dir).resolve()
    batch_dir.mkdir(parents=True, exist_ok=True)
    article_list = list(articles)
    if not article_list:
        raise SyncBatchError("Cannot create a sync batch with no articles.")
    batch_id = generate_sync_batch_id()
    jsonl_path = batch_dir / f"posts-{batch_id}.jsonl"
    metadata_path = batch_dir / f"posts-{batch_id}.metadata.json"
    jsonl_tmp_path = batch_dir / f"{jsonl_path.name}.tmp"
    metadata_tmp_path = batch_dir / f"{metadata_path.name}.tmp"
    try:
        article_count = export_articles_jsonl(jsonl_tmp_path, article_list)
        metadata = SyncBatchMetadata(
            batch_id=batch_id,
            created_at=utc_now_iso(),
            source_host=socket.gethostname(),
            board_slug=settings.board_slug,
            category_label=settings.category_label,
            article_count=article_count,
            since_post_id=since_post_id,
            min_post_id=min(article.post_id for article in article_list),
            max_post_id=max(article.post_id for article in article_list),
            recheck_posts=recheck_posts,
            max_pages=max_pages,
        )
        metadata_tmp_path.write_text(
            json.dumps(metadata.to_payload(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        jsonl_tmp_path.replace(jsonl_path)
        metadata_tmp_path.replace(metadata_path)
    except Exception:
        jsonl_tmp_path.unlink(missing_ok=True)
        metadata_tmp_path.unlink(missing_ok=True)
        raise
    return SyncBatchExportResult(
        batch=SyncBatchFiles(
            batch_id=batch_id,
            jsonl_path=jsonl_path,
            metadata_path=metadata_path,
        ),
        metadata=metadata,
    )


def generate_sync_batch_id() -> str:
    timestamp = utc_now_iso().replace("-", "").replace(":", "").replace("+00:00", "Z")
    return f"{timestamp}-{secrets.token_hex(3)}"


def list_sync_batches(path: str | Path) -> list[SyncBatchFiles]:
    root = Path(path).resolve()
    root.mkdir(parents=True, exist_ok=True)
    batches: list[SyncBatchFiles] = []
    for metadata_path in sorted(root.glob("posts-*.metadata.json")):
        batch_id = _parse_batch_id_from_metadata_name(metadata_path.name)
        if batch_id is None:
            continue
        jsonl_path = root / f"posts-{batch_id}.jsonl"
        if not jsonl_path.is_file():
            continue
        batches.append(
            SyncBatchFiles(
                batch_id=batch_id,
                jsonl_path=jsonl_path,
                metadata_path=metadata_path,
            )
        )
    return sorted(batches, key=lambda batch: batch.batch_id)


def find_sync_batch(path: str | Path, *, batch_id: str | None = None) -> SyncBatchFiles:
    batches = list_sync_batches(path)
    if not batches:
        raise SyncBatchError(f"No sync batches were found under {Path(path).resolve()}")
    if batch_id is None:
        return batches[-1]
    for batch in batches:
        if batch.batch_id == batch_id:
            return batch
    raise SyncBatchError(f"Sync batch {batch_id!r} was not found under {Path(path).resolve()}")


def load_sync_batch_metadata(batch: SyncBatchFiles) -> SyncBatchMetadata:
    try:
        payload = json.loads(batch.metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SyncBatchError(
            f"{batch.metadata_path} is not valid JSON: {exc}"
        ) from exc
    metadata = SyncBatchMetadata.from_payload(
        payload,
        expected_batch_id=batch.batch_id,
        source_path=batch.metadata_path,
    )
    actual_article_count = count_jsonl_articles(batch.jsonl_path)
    if actual_article_count != metadata.article_count:
        raise SyncBatchError(
            f"{batch.metadata_path} article_count={metadata.article_count} does not match "
            f"{batch.jsonl_path} lines={actual_article_count}"
        )
    return metadata


def count_jsonl_articles(path: str | Path) -> int:
    count = 0
    with Path(path).open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            if raw_line.strip():
                count += 1
    return count


def archive_sync_batch(
    batch: SyncBatchFiles,
    archive_root: str | Path,
    *,
    status: str,
) -> SyncBatchFiles:
    archive_dir = Path(archive_root).resolve() / status
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived_batch = _build_archive_batch(batch.batch_id, archive_dir)
    batch.jsonl_path.replace(archived_batch.jsonl_path)
    batch.metadata_path.replace(archived_batch.metadata_path)
    return archived_batch


def push_sync_batch(batch: SyncBatchFiles, settings: Settings, *, verbose: bool = False) -> None:
    remote_host = settings.sync_remote_host
    remote_user = settings.sync_remote_user
    remote_path = settings.sync_remote_path
    if not remote_host or not remote_user or not remote_path:
        raise SyncConfigurationError(
            "SYNC_REMOTE_HOST, SYNC_REMOTE_USER, and SYNC_REMOTE_PATH must be configured"
        )
    _require_binary("ssh")
    _require_binary("scp")

    remote_dir = PurePosixPath(remote_path)
    remote_target = f"{remote_user}@{remote_host}"
    remote_jsonl = remote_dir / batch.jsonl_path.name
    remote_metadata = remote_dir / batch.metadata_path.name
    remote_jsonl_tmp = PurePosixPath(f"{remote_jsonl.as_posix()}.tmp")
    remote_metadata_tmp = PurePosixPath(f"{remote_metadata.as_posix()}.tmp")

    _run_command(
        [
            "ssh",
            "-p",
            str(settings.sync_ssh_port),
            remote_target,
            f"mkdir -p -- {shlex.quote(remote_dir.as_posix())}",
        ],
        verbose=verbose,
    )
    try:
        _run_command(
            [
                "scp",
                "-P",
                str(settings.sync_ssh_port),
                str(batch.jsonl_path),
                f"{remote_target}:{shlex.quote(remote_jsonl_tmp.as_posix())}",
            ],
            verbose=verbose,
        )
        _run_command(
            [
                "scp",
                "-P",
                str(settings.sync_ssh_port),
                str(batch.metadata_path),
                f"{remote_target}:{shlex.quote(remote_metadata_tmp.as_posix())}",
            ],
            verbose=verbose,
        )
        _run_command(
            [
                "ssh",
                "-p",
                str(settings.sync_ssh_port),
                remote_target,
                (
                    "set -e; "
                    f"test ! -e {shlex.quote(remote_jsonl.as_posix())}; "
                    f"test ! -e {shlex.quote(remote_metadata.as_posix())}; "
                    f"mv -- {shlex.quote(remote_jsonl_tmp.as_posix())} {shlex.quote(remote_jsonl.as_posix())}; "
                    f"mv -- {shlex.quote(remote_metadata_tmp.as_posix())} {shlex.quote(remote_metadata.as_posix())}"
                ),
            ],
            verbose=verbose,
        )
    except SyncTransportError:
        _cleanup_remote_temp_files(
            remote_target,
            settings.sync_ssh_port,
            remote_jsonl_tmp,
            remote_metadata_tmp,
            verbose=verbose,
        )
        raise


def read_remote_sync_status(
    settings: Settings,
    *,
    recent_posts: int = 0,
    verbose: bool = False,
) -> SyncStatus:
    remote_host = settings.sync_remote_host
    remote_user = settings.sync_remote_user
    if not remote_host or not remote_user:
        raise SyncConfigurationError(
            "SYNC_REMOTE_HOST and SYNC_REMOTE_USER must be configured"
        )
    _require_binary("ssh")

    remote_target = f"{remote_user}@{remote_host}"
    stdout = _run_command(
        [
            "ssh",
            "-p",
            str(settings.sync_ssh_port),
            remote_target,
            f"bash -lc {shlex.quote(f'rag sync status --json --recent-posts {recent_posts}')}",
        ],
        verbose=verbose,
    )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise SyncTransportError(
            f"Remote sync status did not return valid JSON: {exc}"
        ) from exc
    return SyncStatus.from_payload(
        payload,
        source=f"{remote_target}:rag sync status --json --recent-posts {recent_posts}",
    )


def _cleanup_remote_temp_files(
    remote_target: str,
    port: int,
    remote_jsonl_tmp: PurePosixPath,
    remote_metadata_tmp: PurePosixPath,
    *,
    verbose: bool,
) -> None:
    try:
        _run_command(
            [
                "ssh",
                "-p",
                str(port),
                remote_target,
                (
                    f"rm -f -- {shlex.quote(remote_jsonl_tmp.as_posix())} "
                    f"{shlex.quote(remote_metadata_tmp.as_posix())}"
                ),
            ],
            verbose=verbose,
        )
    except SyncTransportError:
        pass


def _run_command(command: list[str], *, verbose: bool) -> str:
    if verbose:
        print(f"[sync] run {' '.join(shlex.quote(part) for part in command)}", file=sys.stderr, flush=True)
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or str(exc)
        raise SyncTransportError(detail) from exc


def _require_binary(name: str) -> None:
    if shutil.which(name) is None:
        raise SyncConfigurationError(f"Required executable was not found on PATH: {name}")


def _build_archive_batch(batch_id: str, archive_dir: Path) -> SyncBatchFiles:
    attempt = 0
    while True:
        suffix = "" if attempt == 0 else f"-{attempt}"
        jsonl_path = archive_dir / f"posts-{batch_id}{suffix}.jsonl"
        metadata_path = archive_dir / f"posts-{batch_id}{suffix}.metadata.json"
        if not jsonl_path.exists() and not metadata_path.exists():
            return SyncBatchFiles(
                batch_id=batch_id,
                jsonl_path=jsonl_path,
                metadata_path=metadata_path,
            )
        attempt += 1


def _required_string(payload: dict[str, object], key: str, source_path: Path | str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SyncBatchError(f"{source_path} field {key!r} must be a non-empty string")
    return value.strip()


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return str(value)
    stripped = value.strip()
    return stripped or None


def _optional_non_negative_int(value: object, key: str, source_path: Path | str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or value < 0:
        raise SyncBatchError(f"{source_path} field {key!r} must be null or a non-negative integer")
    return value


def _parse_recent_posts(value: object, source: str) -> list[SyncStatusPost]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SyncBatchError(f"{source} field 'recent_posts' must be a list")
    posts: list[SyncStatusPost] = []
    for item in value:
        if not isinstance(item, dict):
            raise SyncBatchError(f"{source} field 'recent_posts' items must be objects")
        post_id = _optional_non_negative_int(item.get("post_id"), "post_id", source)
        content_hash = item.get("content_hash")
        if post_id is None:
            raise SyncBatchError(f"{source} field 'recent_posts[].post_id' must be a non-negative integer")
        if not isinstance(content_hash, str) or not content_hash.strip():
            raise SyncBatchError(f"{source} field 'recent_posts[].content_hash' must be a non-empty string")
        posts.append(SyncStatusPost(post_id=post_id, content_hash=content_hash.strip()))
    return posts


def _parse_batch_id_from_metadata_name(name: str) -> str | None:
    prefix = "posts-"
    suffix = ".metadata.json"
    if not name.startswith(prefix) or not name.endswith(suffix):
        return None
    batch_id = name[len(prefix) : -len(suffix)]
    return batch_id or None
