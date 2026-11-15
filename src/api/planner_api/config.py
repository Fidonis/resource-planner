"""Configuration, read from environment variables (12-factor)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    # Persistence
    database_url: str = "postgresql+psycopg://planner:planner@localhost:5432/planner"
    migrate_on_start: bool = True

    # Where the browser reaches the application. Decides the `Secure` flag of the cookies and the
    # redirect URI of the OIDC flow.
    public_base_url: str = "http://localhost:8080"
    cors_origins: str = ""
    log_level: str = "INFO"
    # The interactive API page loads its scripts from a public CDN, so it is off by default.
    enable_api_docs: bool = False

    # Sessions and local sign-in
    session_ttl_hours: int = 12
    auth_local_enabled: bool = True
    auth_oidc_enabled: bool = False
    password_min_length: int = 12
    login_max_attempts: int = 5
    login_lockout_minutes: int = 15
    bootstrap_admin_user: str = ""
    bootstrap_admin_password: str = ""

    # OIDC (the API owns the flow, the UI only forwards the session cookie)
    oidc_issuer: str = ""
    oidc_discovery_url: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_scopes: str = "openid profile email"
    oidc_roles_claim: str = "realm_access.roles"
    oidc_roles_source: Literal["id_token", "access_token", "userinfo"] = "access_token"
    oidc_username_claim: str = "preferred_username"
    oidc_provider_id: str = "oidc"
    oidc_merge_local_roles: bool = False
    # Audiences accepted on bearer tokens; empty disables bearer validation.
    oidc_api_audiences: str = ""

    # Secrets at rest (integration configuration)
    app_encryption_key: str = ""

    # Demo data and imports
    enable_demo_seed: bool = False
    import_dir: Path = Path("/data/imports")

    # Scheduler for integrations. Run one API worker or accept that every worker schedules; the
    # advisory lock still keeps a single run per integration.
    scheduler_enabled: bool = True
    # A scheduled run of a provider that can read only what changed reads the whole source when
    # the last complete read is older than this; only such a run closes what has disappeared.
    sync_full_interval_hours: int = Field(default=24, ge=1, le=24 * 30)

    @field_validator("public_base_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @model_validator(mode="after")
    def _need_one_auth_mode(self) -> Settings:
        if not (self.auth_local_enabled or self.auth_oidc_enabled):
            raise ValueError("At least one of AUTH_LOCAL_ENABLED and AUTH_OIDC_ENABLED must be on")
        return self

    @model_validator(mode="after")
    def _oidc_needs_its_settings(self) -> Settings:
        if self.auth_oidc_enabled and not (self.oidc_issuer and self.oidc_client_id):
            raise ValueError("AUTH_OIDC_ENABLED needs OIDC_ISSUER and OIDC_CLIENT_ID")
        return self

    @property
    def cookie_secure(self) -> bool:
        return self.public_base_url.startswith("https://")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def oidc_audience_list(self) -> list[str]:
        return [a.strip() for a in self.oidc_api_audiences.split(",") if a.strip()]

    @property
    def oidc_redirect_uri(self) -> str:
        return f"{self.public_base_url}/api/v1/auth/oidc/callback"


@lru_cache
def get_settings() -> Settings:
    return Settings()
