"""Schemas of the settings endpoint."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from planner_api.schemas.common import ApiModel

Locale = Literal["en", "de"]


class SettingsIn(ApiModel):
    planning_horizon_weeks: int = Field(ge=1, le=12, description="Weeks the board shows.")
    default_allocation_percent: int = Field(
        ge=10, le=100, description="Allocation proposed for a new member."
    )
    locale: Locale = Field(description="Interface language of accounts without a preference.")


class SettingsOut(SettingsIn):
    auth_local_enabled: bool = Field(description="Set by AUTH_LOCAL_ENABLED; read-only here.")
    auth_oidc_enabled: bool = Field(description="Set by AUTH_OIDC_ENABLED; read-only here.")
    demo_seed_enabled: bool = Field(description="Set by ENABLE_DEMO_SEED; read-only here.")
