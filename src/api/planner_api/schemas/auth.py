"""Schemas of the authentication endpoints."""

from __future__ import annotations

import uuid

from pydantic import Field

from planner_api.schemas.common import ApiModel
from planner_api.schemas.settings import Locale


class AuthConfig(ApiModel):
    local_enabled: bool
    oidc_enabled: bool
    oidc_login_url: str | None = Field(
        description="Where to send the browser to sign in with the identity provider."
    )
    password_min_length: int
    default_locale: str


class LoginRequest(ApiModel):
    username: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=1, max_length=1000)


class GrantOut(ApiModel):
    permission: str
    team_ids: list[uuid.UUID] | None = Field(
        description="Teams the permission is limited to; null means every team."
    )


class Me(ApiModel):
    user_id: uuid.UUID | None
    username: str
    display_name: str
    email: str | None = None
    auth_provider: str
    locale: str | None
    must_change_password: bool
    member_id: uuid.UUID | None
    permissions: list[str]
    grants: list[GrantOut]
    csrf_token: str | None = Field(
        description="Send as X-CSRF-Token on every change made with the session cookie."
    )
    via: str


class ChangePasswordRequest(ApiModel):
    current_password: str | None = Field(
        default=None, description="Required unless the password change was forced."
    )
    new_password: str = Field(min_length=1, max_length=1000)


class PreferencesRequest(ApiModel):
    locale: Locale | None = Field(
        default=None, description="Interface language; null follows the server default."
    )


class LogoutResult(ApiModel):
    redirect_url: str | None = Field(
        description="Identity-provider logout page to visit next (OIDC sessions), if any."
    )
