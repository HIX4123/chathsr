from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


DEFAULT_BOARD_SLUG = "hkstarrail"
DEFAULT_CATEGORY_LABEL = "정보"
DEFAULT_GENERATION_MODEL = "gemini-3-flash-preview"
DEFAULT_FALLBACK_GENERATION_MODEL = "gemini-3.1-flash-lite-preview"
DEFAULT_EMBEDDING_MODEL = "gemini-embedding-2-preview"
DEFAULT_EMBEDDING_DIM = 1536
DEFAULT_TOP_K = 6
DEFAULT_REMOTE_SYNC_SSH_PORT = 22
DEFAULT_REMOTE_SYNC_POLL_INTERVAL_SECONDS = 30


@dataclass(slots=True)
class Settings:
    project_root: Path
    data_dir: Path
    database_path: Path
    sync_inbox_dir: Path
    sync_archive_dir: Path
    sync_client_outbox_dir: Path
    sync_remote_host: str | None
    sync_remote_user: str | None
    sync_remote_path: str | None
    sync_ssh_port: int
    gemini_api_key: str | None
    generation_model: str
    cheap_generation_model: str
    embedding_model: str
    embedding_dim: int
    board_slug: str
    category_label: str
    top_k: int
    remote_sync_ssh_host: str | None = None
    remote_sync_ssh_user: str | None = None
    remote_sync_ssh_port: int = DEFAULT_REMOTE_SYNC_SSH_PORT
    remote_sync_ssh_identity_file: Path | None = None
    remote_sync_remote_inbox_dir: str | None = None
    remote_sync_remote_rag_bin: str | None = None
    remote_sync_poll_interval_seconds: int = (
        DEFAULT_REMOTE_SYNC_POLL_INTERVAL_SECONDS
    )

    @property
    def board_url(self) -> str:
        return f"https://arca.live/b/{self.board_slug}"

    @property
    def embedding_space_version(self) -> str:
        return f"{self.embedding_model}@{self.embedding_dim}"

    @property
    def failed_posts_dir(self) -> Path:
        return self.data_dir / "failed-posts"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.sync_inbox_dir.mkdir(parents=True, exist_ok=True)
        self.sync_archive_dir.mkdir(parents=True, exist_ok=True)
        self.sync_client_outbox_dir.mkdir(parents=True, exist_ok=True)


def load_settings(project_root: str | Path | None = None) -> Settings:
    load_dotenv()
    root = Path(project_root or os.getcwd()).resolve()
    data_dir = Path(os.getenv("DATA_DIR", root / "data")).resolve()
    database_path = Path(
        os.getenv("DATABASE_PATH", data_dir / "chathsr.sqlite3")
    ).resolve()
    settings = Settings(
        project_root=root,
        data_dir=data_dir,
        database_path=database_path,
        sync_inbox_dir=Path(
            os.getenv("SYNC_INBOX_DIR", data_dir / "inbox")
        ).resolve(),
        sync_archive_dir=Path(
            os.getenv("SYNC_ARCHIVE_DIR", data_dir / "sync-archive")
        ).resolve(),
        sync_client_outbox_dir=Path(
            os.getenv("SYNC_CLIENT_OUTBOX_DIR", data_dir / "sync-outbox")
        ).resolve(),
        sync_remote_host=os.getenv("SYNC_REMOTE_HOST"),
        sync_remote_user=os.getenv("SYNC_REMOTE_USER"),
        sync_remote_path=os.getenv("SYNC_REMOTE_PATH"),
        sync_ssh_port=int(os.getenv("SYNC_SSH_PORT", "22")),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        generation_model=os.getenv("GENERATION_MODEL", DEFAULT_GENERATION_MODEL),
        cheap_generation_model=os.getenv(
            "CHEAP_GENERATION_MODEL", DEFAULT_FALLBACK_GENERATION_MODEL
        ),
        embedding_model=os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        embedding_dim=int(os.getenv("EMBEDDING_DIM", str(DEFAULT_EMBEDDING_DIM))),
        board_slug=os.getenv("BOARD_SLUG", DEFAULT_BOARD_SLUG),
        category_label=os.getenv("CATEGORY_LABEL", DEFAULT_CATEGORY_LABEL),
        top_k=int(os.getenv("TOP_K", str(DEFAULT_TOP_K))),
        remote_sync_ssh_host=_optional_env("REMOTE_SYNC_SSH_HOST"),
        remote_sync_ssh_user=_optional_env("REMOTE_SYNC_SSH_USER"),
        remote_sync_ssh_port=int(
            os.getenv(
                "REMOTE_SYNC_SSH_PORT",
                str(DEFAULT_REMOTE_SYNC_SSH_PORT),
            )
        ),
        remote_sync_ssh_identity_file=_optional_path_env(
            "REMOTE_SYNC_SSH_IDENTITY_FILE"
        ),
        remote_sync_remote_inbox_dir=_optional_env("REMOTE_SYNC_REMOTE_INBOX_DIR"),
        remote_sync_remote_rag_bin=_optional_env("REMOTE_SYNC_REMOTE_RAG_BIN"),
        remote_sync_poll_interval_seconds=int(
            os.getenv(
                "REMOTE_SYNC_POLL_INTERVAL_SECONDS",
                str(DEFAULT_REMOTE_SYNC_POLL_INTERVAL_SECONDS),
            )
        ),
    )
    settings.ensure_directories()
    return settings


def _optional_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _optional_path_env(name: str) -> Path | None:
    value = _optional_env(name)
    if value is None:
        return None
    return Path(value).expanduser().resolve()
