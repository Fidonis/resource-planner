"""Permission catalogue (§8.3) and the built-in roles (§8.4). Code checks permissions only."""

from __future__ import annotations

from dataclasses import dataclass

TEAM_READ = "team:read"
TEAM_WRITE = "team:write"
SKILL_READ = "skill:read"
SKILL_WRITE = "skill:write"
MEMBER_READ = "member:read"
MEMBER_WRITE = "member:write"
TASK_READ = "task:read"
TASK_WRITE = "task:write"
ASSIGNMENT_READ = "assignment:read"
ASSIGNMENT_WRITE = "assignment:write"
ABSENCE_READ = "absence:read"
ABSENCE_WRITE = "absence:write"
ABSENCE_WRITE_OWN = "absence:write:own"
ABSENCE_TYPE_READ = "absence-type:read"
ABSENCE_TYPE_WRITE = "absence-type:write"
USER_ADMIN = "user:admin"
ROLE_ADMIN = "role:admin"
TOKEN_ADMIN = "token:admin"
INTEGRATION_ADMIN = "integration:admin"
INGEST_WRITE = "ingest:write"
SETTINGS_ADMIN = "settings:admin"
AUDIT_READ = "audit:read"

DESCRIPTIONS: dict[str, str] = {
    TEAM_READ: "View teams",
    TEAM_WRITE: "Create, edit and delete teams",
    SKILL_READ: "View skills",
    SKILL_WRITE: "Manage skills",
    MEMBER_READ: "View members",
    MEMBER_WRITE: "Manage members, their skills, allocation and team",
    TASK_READ: "View tasks and efforts",
    TASK_WRITE: "Manage tasks and efforts",
    ASSIGNMENT_READ: "View the planning, overview and availability",
    ASSIGNMENT_WRITE: "Create, move and delete assignments",
    ABSENCE_READ: "View all absences",
    ABSENCE_WRITE: "Manage absences of any member in scope",
    ABSENCE_WRITE_OWN: "Manage absences of the member linked to the own account",
    ABSENCE_TYPE_READ: "View absence types",
    ABSENCE_TYPE_WRITE: "Manage absence types",
    USER_ADMIN: "Manage local users",
    ROLE_ADMIN: "Manage roles and the mapping of external roles",
    TOKEN_ADMIN: "Manage API tokens of any user",
    INTEGRATION_ADMIN: "Manage integrations and run syncs",
    INGEST_WRITE: "Use the machine ingest API",
    SETTINGS_ADMIN: "Change settings",
    AUDIT_READ: "Read the audit log",
}

ALL_PERMISSIONS: tuple[str, ...] = tuple(DESCRIPTIONS)

# A role restricted to teams limits only these permissions; every other permission it grants
# applies to all teams (a read permission has nothing to restrict).
TEAM_SCOPED: frozenset[str] = frozenset({MEMBER_WRITE, ASSIGNMENT_WRITE, ABSENCE_WRITE})

VIEWER_PERMISSIONS: tuple[str, ...] = (
    TEAM_READ,
    SKILL_READ,
    MEMBER_READ,
    TASK_READ,
    ASSIGNMENT_READ,
    ABSENCE_READ,
    ABSENCE_TYPE_READ,
)


@dataclass(frozen=True, slots=True)
class BuiltinRole:
    key: str
    name: str
    description: str
    permissions: tuple[str, ...]


BUILTIN_ROLES: tuple[BuiltinRole, ...] = (
    BuiltinRole("viewer", "Viewer", "All read permissions", VIEWER_PERMISSIONS),
    BuiltinRole(
        "member",
        "Member",
        "Viewer plus own absences",
        (*VIEWER_PERMISSIONS, ABSENCE_WRITE_OWN),
    ),
    BuiltinRole(
        "team-lead",
        "Team lead",
        "Viewer plus planning for the teams the role is scoped to",
        (*VIEWER_PERMISSIONS, ASSIGNMENT_WRITE, ABSENCE_WRITE, MEMBER_WRITE),
    ),
    BuiltinRole(
        "planner",
        "Planner",
        "Viewer plus tasks, assignments and absences of all teams",
        (*VIEWER_PERMISSIONS, TASK_WRITE, ASSIGNMENT_WRITE, ABSENCE_WRITE),
    ),
    BuiltinRole(
        "master-data-admin",
        "Master data admin",
        "Viewer plus teams, skills, members and absence types",
        (*VIEWER_PERMISSIONS, TEAM_WRITE, SKILL_WRITE, MEMBER_WRITE, ABSENCE_TYPE_WRITE),
    ),
    BuiltinRole("admin", "Administrator", "Everything", ALL_PERMISSIONS),
)

# Default mapping of Keycloak realm roles to internal roles (editable afterwards).
DEFAULT_ROLE_MAPPING: dict[str, str] = {
    "rp-admin": "admin",
    "rp-planner": "planner",
    "rp-viewer": "viewer",
}
