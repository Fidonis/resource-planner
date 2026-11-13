"""The navigation: which entries exist and which of them a user gets to see."""

from __future__ import annotations

from dataclasses import dataclass

from planner_ui.i18n import N_, gettext_
from planner_ui.session import UiContext


@dataclass(frozen=True)
class NavItem:
    href: str
    icon: str
    label: str  # message id
    permission: str
    group: str


GROUPS: tuple[tuple[str, str], ...] = (
    ("planning", N_("Planning")),
    ("master", N_("Master data")),
    ("admin", N_("Administration")),
)

ITEMS: tuple[NavItem, ...] = (
    NavItem("/planning", "layout-kanban", N_("Planning board"), "assignment:read", "planning"),
    NavItem("/overview", "layout-dashboard", N_("Overview"), "assignment:read", "planning"),
    NavItem("/tasks", "list-check", N_("Tasks"), "task:read", "planning"),
    NavItem("/absences", "calendar", N_("Absences"), "absence:read", "planning"),
    NavItem("/teams", "users-group", N_("Teams"), "team:read", "master"),
    NavItem("/members", "users", N_("Members"), "member:read", "master"),
    NavItem("/skills", "certificate", N_("Skills"), "skill:read", "master"),
    NavItem("/absence-types", "calendar-off", N_("Absence types"), "absence-type:read", "master"),
    NavItem("/users", "user", N_("Users"), "user:admin", "admin"),
    NavItem("/roles", "shield-lock", N_("Roles"), "role:admin", "admin"),
    NavItem("/api-tokens", "key", N_("API tokens"), "token:admin", "admin"),
    NavItem("/integrations", "plug-connected", N_("Integrations"), "integration:admin", "admin"),
    NavItem("/audit", "history", N_("Audit log"), "audit:read", "admin"),
    NavItem("/settings", "settings", N_("Settings"), "settings:admin", "admin"),
)


@dataclass(frozen=True)
class NavGroup:
    label: str
    items: list[NavItem]


def visible(ctx: UiContext) -> list[NavGroup]:
    groups: list[NavGroup] = []
    for key, label in GROUPS:
        items = [item for item in ITEMS if item.group == key and ctx.can(item.permission)]
        if items:
            groups.append(NavGroup(gettext_(label), items))
    return groups


def is_active(item: NavItem, path: str) -> bool:
    return path == item.href or path.startswith(item.href + "/")
