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
DEFAULT_STORAGE_STATE_FILENAME = "storage_state.json"


@dataclass(slots=True)
class Settings:
    project_root: Path
    data_dir: Path
    database_path: Path
    playwright_profile_dir: Path
    playwright_storage_state_path: Path
    playwright_storage_state_path_configured: bool
    gemini_api_key: str | None
    generation_model: str
    cheap_generation_model: str
    embedding_model: str
    embedding_dim: int
    board_slug: str
    category_label: str
    top_k: int

    @property
    def board_url(self) -> str:
        return f"https://arca.live/b/{self.board_slug}"

    @property
    def embedding_space_version(self) -> str:
        return f"{self.embedding_model}@{self.embedding_dim}"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.playwright_profile_dir.mkdir(parents=True, exist_ok=True)
        self.playwright_storage_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def should_use_storage_state(self) -> bool:
        return (
            self.playwright_storage_state_path_configured
            or self.playwright_storage_state_path.exists()
        )


def load_settings(project_root: str | Path | None = None) -> Settings:
    load_dotenv()
    root = Path(project_root or os.getcwd()).resolve()
    data_dir = Path(os.getenv("DATA_DIR", root / "data")).resolve()
    database_path = Path(
        os.getenv("DATABASE_PATH", data_dir / "chathsr.sqlite3")
    ).resolve()
    profile_dir = Path(
        os.getenv("PLAYWRIGHT_PROFILE_DIR", data_dir / "playwright-profile")
    ).resolve()
    storage_state_env = os.getenv("PLAYWRIGHT_STORAGE_STATE_PATH")
    storage_state_path = Path(
        storage_state_env or data_dir / DEFAULT_STORAGE_STATE_FILENAME
    ).resolve()
    settings = Settings(
        project_root=root,
        data_dir=data_dir,
        database_path=database_path,
        playwright_profile_dir=profile_dir,
        playwright_storage_state_path=storage_state_path,
        playwright_storage_state_path_configured=storage_state_env is not None,
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
    )
    settings.ensure_directories()
    return settings
