"""Application configuration using Pydantic Settings."""

from functools import lru_cache

from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str = Field(description="Telegram Bot API token")
    deepgram_api_key: str = Field(description="Deepgram API key for transcription")
    anthropic_api_key: str = Field(default="", description="Anthropic API key for Claude")
    todoist_api_key: str = Field(default="", description="Todoist API key for tasks")
    youtube_api_key: str = Field(default="", description="YouTube Data API v3 key")
    firecrawl_api_key: str = Field(default="", description="Firecrawl API key for web scraping")
    vault_path: Path = Field(
        default=Path("./vault"),
        description="Path to Obsidian vault directory",
    )
    allowed_user_ids: list[int] = Field(
        default_factory=list,
        description="List of Telegram user IDs allowed to use the bot",
    )
    allow_all_users: bool = Field(
        default=False,
        description="Whether to allow access to all users (security risk!)",
    )


    # Feature toggles
    ddoctor_enabled: bool = Field(
        default=False,
        description="Enable d-doctor integration (injects nutrition + Oura context into d-brain REFLECT)",
    )

    obsidian_sync_enabled: bool = Field(
        default=True,
        description="Push to git after each saved message (Obsidian real-time sync)",
    )

    improve_mode: bool = Field(
        default=False,
        description="Show 'Улучшить' shortcut button in main keyboard",
    )

    first_seen: Optional[str] = Field(
        default=None,
        description="Date when user first used the bot (YYYY-MM-DD), for onboarding help button",
    )

    # Location (updated dynamically by /location command)
    location_city: str = Field(default="Москва", description="Current city name")
    location_lat: float = Field(default=55.75, description="Current latitude")
    location_lon: float = Field(default=37.62, description="Current longitude")
    location_tz: str = Field(default="Europe/Moscow", description="Current IANA timezone")

    # ── persistent tmux session (ASB v3.0 billing migration) ─────────────
    # The bot drives ONE long-lived INTERACTIVE Claude Code session in tmux
    # instead of headless `claude -p` (which, since 2026-06-15, bills against
    # a separate paid Agent SDK credit). Interactive usage stays on the
    # subscription.
    runtime_dir: Path = Field(
        default_factory=lambda: Path.home() / ".dbrain",
        description="Runtime dir for pane.lock, pane.log, ready/inflight flags (LOCAL fs)",
    )
    brain_session_name: str = Field(
        default="",
        description="tmux session name (empty → generated & persisted per install)",
    )
    claude_model: str = Field(
        default="claude-opus-4-8",
        description="Model for the persistent session (empty = Claude Code default)",
    )
    tz: str = Field(default="Europe/Moscow", description="Timezone for timers/reports")

    # ── cron (scheduled jobs in the second, isolated brain session) ──────
    cron_enabled: bool = Field(default=True, description="Run the in-bot cron ticker")
    cron_tick_seconds: float = Field(
        default=60.0, description="Ticker interval; jobs.json is re-read every tick"
    )
    cron_job_timeout: float = Field(
        default=600.0, description="Per-job ask() timeout in the cron session"
    )
    cron_max_consecutive_errors: int = Field(
        default=3, description="Consecutive failures before a job is auto-disabled"
    )
    cron_retry_seconds: float = Field(
        default=300.0, description="Retry delay for a failed one-shot ('at') job"
    )

    @field_validator("runtime_dir", "vault_path", mode="after")
    @classmethod
    def _expand_user(cls, v: Path) -> Path:
        # pydantic-settings keeps a literal "~"; the cron CLI / scripts
        # expanduser — expand here too or state dirs split apart.
        return v.expanduser()

    @property
    def cron_dir(self) -> Path:
        """Cron state dir: jobs.json + the cron session's runtime files.

        Matches the CLI default (RUNTIME_DIR/cron) so the brain's
        `python -m d_brain.cron` edits and the in-bot ticker share one file.
        """
        return self.runtime_dir / "cron"

    @property
    def admin_chat_id(self) -> Optional[int]:
        """First allowed user — destination for health alerts / reports."""
        return self.allowed_user_ids[0] if self.allowed_user_ids else None

    @property
    def daily_path(self) -> Path:
        """Path to daily notes directory."""
        return self.vault_path / "daily"

    @property
    def attachments_path(self) -> Path:
        """Path to attachments directory."""
        return self.vault_path / "attachments"

    @property
    def thoughts_path(self) -> Path:
        """Path to thoughts directory."""
        return self.vault_path / "thoughts"


@lru_cache
def get_settings() -> Settings:
    """Get application settings instance."""
    return Settings()
