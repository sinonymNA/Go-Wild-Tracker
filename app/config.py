from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./gowild.db"
    ADMIN_PASSWORD: str  # required — no default
    MOCK_MODE: bool = False
    PORT: int = 8000
    DISCORD_WEBHOOK_URL: str | None = None
    HOME_AIRPORT: str = "ATL"
    SCAN_INTERVAL_MINUTES: int = 60
    MAX_ROUTES_PER_SCAN: int = 10
    DELAY_BETWEEN_SEARCHES_SECONDS: float = 5.0
    SCAN_DATES_AHEAD: int = 90
    # Skip a (route, date) pair that was scanned within this many hours and
    # was unavailable. Set to 0 to always rescan everything. Available results
    # are always rescanned regardless of this setting.
    SCAN_STALE_HOURS: int = 6
    HEADLESS: bool = True
    DEBUG_SCREENSHOTS: bool = False
    SECRET_KEY: str = "change-me-in-production"
    PROXY_URL: str | None = None  # e.g. http://user:pass@gate.smartproxy.com:10000

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
