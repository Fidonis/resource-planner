"""Schemas of the settings endpoint."""

from __future__ import annotations

from typing import Annotated, Literal, get_args

from pydantic import AfterValidator, Field

from planner_api.schemas.common import ApiModel

Locale = Literal["en", "de"]

# What the board can show next to a task to plan, in the order it shows them.
PoolTaskField = Literal[
    "due_date",
    "start_date",
    "original_estimate_hours",
    "story_points",
    "priority",
    "assignee",
    "reporter",
    "owner_team",
    "source",
    "open_effort",
]
POOL_TASK_FIELDS: tuple[str, ...] = get_args(PoolTaskField)


def ordered_pool_fields(values: list[PoolTaskField]) -> list[PoolTaskField]:
    """The board's order with every field once, however a client lists them."""
    chosen = set(values)
    return [f for f in get_args(PoolTaskField) if f in chosen]


PoolTaskFields = Annotated[list[PoolTaskField], AfterValidator(ordered_pool_fields)]


class SettingsIn(ApiModel):
    planning_horizon_weeks: int = Field(ge=1, le=12, description="Weeks the board shows.")
    default_allocation_percent: int = Field(
        ge=10, le=100, description="Allocation proposed for a new member."
    )
    locale: Locale = Field(description="Interface language of accounts without a preference.")
    # A plain default (pydantic copies it) puts it into the schema, so clients default to it too.
    pool_task_fields: PoolTaskFields = Field(
        default=[],
        description="Attributes the board shows with each task to plan, for accounts that have "
        "not chosen their own. Kept in the board's order; a field listed twice counts once.",
        examples=[["due_date", "story_points"]],
    )


class SettingsOut(SettingsIn):
    auth_local_enabled: bool = Field(description="Set by AUTH_LOCAL_ENABLED; read-only here.")
    auth_oidc_enabled: bool = Field(description="Set by AUTH_OIDC_ENABLED; read-only here.")
    demo_seed_enabled: bool = Field(description="Set by ENABLE_DEMO_SEED; read-only here.")
