"""Application configuration using Pydantic Settings."""

from functools import lru_cache

from pathlib import Path
from typing import Optional

from pydantic import Field
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
    supabase_url: str = Field(default="", description="Supabase project URL (e.g. https://xxx.supabase.co)")
    supabase_key: str = Field(default="", description="Supabase service_role key")
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
    health_enabled: bool = Field(
        default=False,
        description="Enable Oura Ring health module (requires OURA_ACCESS_TOKEN)",
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


    # Nutrition profile (used by nutritionist sub-agent)
    # Calculate your targets: https://www.calculator.net/calorie-calculator.html
    nutrition_height_cm: int = Field(default=175, description="Height in cm")
    nutrition_weight_kg: float = Field(default=80.0, description="Current weight in kg")
    nutrition_age: int = Field(default=30, description="Age in years")
    nutrition_gender: str = Field(default="мужчина", description="Gender (мужчина/женщина)")
    nutrition_activity: str = Field(default="умеренная активность", description="Activity level description")
    nutrition_goal: str = Field(default="поддерживать вес", description="Nutrition goal description")
    nutrition_notes: str = Field(default="", description="Dietary restrictions or preferences")
    nutrition_daily_kcal: int = Field(default=2000, description="Daily calorie target (kcal)")
    nutrition_daily_protein: float = Field(default=150.0, description="Daily protein target (g)")
    nutrition_daily_fat: float = Field(default=55.0, description="Daily fat target (g)")
    nutrition_daily_carbs: float = Field(default=220.0, description="Daily carbs target (g)")

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
