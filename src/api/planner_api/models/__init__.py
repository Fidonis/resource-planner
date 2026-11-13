"""ORM models. Importing this package registers every table on ``Base.metadata``."""

from planner_api.models.absence import Absence, AbsenceType
from planner_api.models.auth import (
    ApiToken,
    AppSession,
    AppUser,
    ExternalRoleMapping,
    LoginState,
    Role,
    RolePermission,
    UserRole,
    UserRoleTeam,
)
from planner_api.models.base import Base
from planner_api.models.org import Member, MemberSkill, Skill, Team
from planner_api.models.planning import Assignment, Task, TaskEffort
from planner_api.models.system import AppSetting, AuditLog, IntegrationConfig, SyncRun

__all__ = [
    "Absence",
    "AbsenceType",
    "ApiToken",
    "AppSession",
    "AppSetting",
    "AppUser",
    "Assignment",
    "AuditLog",
    "Base",
    "ExternalRoleMapping",
    "IntegrationConfig",
    "LoginState",
    "Member",
    "MemberSkill",
    "Role",
    "RolePermission",
    "Skill",
    "SyncRun",
    "Task",
    "TaskEffort",
    "Team",
    "UserRole",
    "UserRoleTeam",
]
