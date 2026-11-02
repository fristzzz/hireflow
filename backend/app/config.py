"""集中管理运行配置与本地存储路径。"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用运行配置。"""

    database_url: str = "postgresql+psycopg://hireflow:hireflow@db:5432/hireflow"
    data_dir: Path = Path("data")
    openai_base_url: str | None = None
    openai_api_key: str = ""
    text_model: str = "gpt-4o-mini"
    vision_model: str = "gpt-4o-mini"
    vision_enabled: bool = True
    max_concurrency: int = 5
    max_file_mb: int = 20
    max_pdf_pages: int = 50
    max_resumes_per_job: int = 200
    model_timeout_seconds: float = 120
    model_max_retries: int = 2
    imap_host: str | None = None
    imap_port: int = 993
    imap_user: str | None = None
    imap_password: str | None = None
    imap_mailbox: str = "INBOX"
    imap_poll_seconds: int = 300
    admin_username: str = "admin"
    admin_password: str = ""
    session_secret: str = ""
    session_max_age: int = 604800

    @property
    def imap_enabled(self) -> bool:
        return bool(self.imap_host and self.imap_user and self.imap_password)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def derived_dir(self) -> Path:
        return self.data_dir / "derived"

    def ensure_directories(self) -> None:
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.derived_dir.mkdir(parents=True, exist_ok=True)
