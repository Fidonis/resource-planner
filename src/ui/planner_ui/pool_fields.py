"""What the board can show with each task to plan, in the order the board shows it.

The ids are the values of ``PoolTaskField`` in the API; the settings set them for everyone and an
account may choose its own.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from planner_client import models
from planner_ui.crud import Choice
from planner_ui.i18n import N_, gettext_


@dataclass(frozen=True)
class PoolField:
    id: str
    label: str  # message id
    icon: str | None  # None when the value names itself, such as "5 SP"


POOL_FIELDS: tuple[PoolField, ...] = (
    PoolField("due_date", N_("Due date"), "calendar-due"),
    PoolField("start_date", N_("Start date"), "calendar-event"),
    PoolField("original_estimate_hours", N_("Original estimate"), "clock"),
    PoolField("story_points", N_("Story points"), None),
    PoolField("priority", N_("Priority"), "flag"),
    PoolField("assignee", N_("Assignee"), "user"),
    PoolField("reporter", N_("Reporter"), "user-edit"),
    PoolField("owner_team", N_("Team"), "users-group"),
    PoolField("source", N_("Source"), "plug-connected"),
    PoolField("open_effort", N_("Open effort"), "hourglass"),
)
POOL_FIELD_IDS = frozenset(f.id for f in POOL_FIELDS)


@dataclass(frozen=True)
class PoolFieldView:
    """A chosen field with its label in the request's language, for the board."""

    id: str
    label: str
    icon: str | None


def choices() -> list[Choice]:
    return [Choice(f.id, gettext_(f.label)) for f in POOL_FIELDS]


def ids(values: Iterable[models.PoolTaskField | str] | None) -> list[str]:
    """Plain ids of the fields the API returned, or of the boxes a form ticked."""
    if values is None:
        return []
    return [str(v.value if isinstance(v, models.PoolTaskField) else v) for v in values]


def labels(values: Iterable[models.PoolTaskField | str] | None) -> list[str]:
    chosen = set(ids(values))
    return [gettext_(f.label) for f in POOL_FIELDS if f.id in chosen]


def shown(values: Iterable[models.PoolTaskField | str] | None) -> list[PoolFieldView]:
    chosen = set(ids(values))
    return [PoolFieldView(f.id, gettext_(f.label), f.icon) for f in POOL_FIELDS if f.id in chosen]
