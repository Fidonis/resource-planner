"""Configuration of the web interface, read from environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    # Where the interface reaches the API (the compose network, not the browser's address).
    api_base_url: str = "http://api:8000"
    api_timeout_seconds: float = 15.0
    # Where the browser reaches the application. https:// switches the cookies to Secure.
    public_base_url: str = "http://localhost:8080"
    log_level: str = "INFO"
    default_locale: str = "en"

    @field_validator("api_base_url", "public_base_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def cookie_secure(self) -> bool:
        return self.public_base_url.startswith("https://")


@lru_cache
def get_settings() -> Settings:
    return Settings()
