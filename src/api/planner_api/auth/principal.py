"""The authenticated caller and what it may do."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from planner_api.auth.permissions import TEAM_SCOPED
from planner_api.errors import forbidden

# permission -> teams it applies to; ``None`` means every team.
Scope = frozenset[uuid.UUID] | None
Grants = Mapping[str, Scope]


def union_scope(a: Scope, b: Scope) -> Scope:
    """Union of two scopes where ``None`` (all teams) wins."""
    if a is None or b is None:
        return None
    return a | b


@dataclass(frozen=True, slots=True)
class RoleGrant:
    """One role assigned to a user, with the permissions it carries and its team scope."""

    permissions: frozenset[str]
    teams: frozenset[uuid.UUID] | None  # None = every team


def grants_from_roles(roles: Iterable[RoleGrant]) -> dict[str, Scope]:
    grants: dict[str, Scope] = {}
    for role in roles:
        for permission in role.permissions:
            scope: Scope = role.teams if permission in TEAM_SCOPED else None
            grants[permission] = (
                union_scope(grants[permission], scope) if permission in grants else scope
            )
    return grants


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: uuid.UUID | None
    username: str
    display_name: str
    grants: Grants
    member_id: uuid.UUID | None = None
    must_change_password: bool = False
    locale: str | None = None
    session_csrf: str | None = field(default=None, repr=False)

    def has(self, permission: str) -> bool:
        return permission in self.grants

    def scope(self, permission: str) -> Scope:
        """Teams ``permission`` applies to. Only meaningful when :meth:`has` is true."""
        return self.grants.get(permission)

    def allows_team(self, permission: str, team_id: uuid.UUID) -> bool:
        if permission not in self.grants:
            return False
        scope = self.grants[permission]
        return scope is None or team_id in scope

    def require(self, permission: str) -> None:
        if permission not in self.grants:
            raise forbidden(permission)

    def require_team(self, permission: str, team_id: uuid.UUID) -> None:
        if not self.allows_team(permission, team_id):
            raise forbidden(permission)

    def restrict(self, allowed: Iterable[str]) -> Principal:
        """The same caller limited to ``allowed`` permissions (API tokens)."""
        keep = set(allowed)
        grants = {p: s for p, s in self.grants.items() if p in keep}
        return Principal(
            self.user_id,
            self.username,
            self.display_name,
            grants,
            self.member_id,
            self.must_change_password,
            self.locale,
            self.session_csrf,
        )
