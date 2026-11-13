"""Schemas of users, roles, role mappings and API tokens."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from planner_api.schemas.common import ApiModel, Stamped
from planner_api.schemas.org import Email, Name, Versioned

Username = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._@+-]{0,159}$")]
RoleKey = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
Description = Annotated[str, StringConstraints(max_length=300)]
Permission = Annotated[str, StringConstraints(min_length=1, max_length=64)]

# --- users -------------------------------------------------------------------------------


class UserRoleIn(ApiModel):
    role_id: uuid.UUID
    team_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description=(
            "Teams the role is limited to. Empty means every team. Only the write permissions "
            "on members, assignments and absences are limited by it."
        ),
    )


class UserRoleOut(ApiModel):
    role_id: uuid.UUID
    role_key: str
    role_name: str
    team_ids: list[uuid.UUID]
    team_names: list[str]


class UserIn(ApiModel):
    username: Username = Field(examples=["zoe"])
    display_name: Name
    email: Email | None = None
    password: str | None = Field(
        default=None,
        max_length=1000,
        description=(
            "First password. Left out, the server generates one and returns it once. "
            "The user has to choose a new one at the first sign-in."
        ),
    )
    active: bool = True
    roles: list[UserRoleIn] = Field(default_factory=list)
    member_id: uuid.UUID | None = Field(
        default=None, description="The member this account stands for (own absences)."
    )


class UserUpdate(Versioned):
    display_name: Name
    email: Email | None = None
    active: bool
    roles: list[UserRoleIn] = Field(default_factory=list)
    member_id: uuid.UUID | None = None


class UserOut(Stamped):
    username: str
    display_name: str
    email: str | None
    auth_provider: str = Field(description="`local` or `oidc:<provider>`.")
    active: bool
    must_change_password: bool
    locked: bool = Field(description="Whether sign-in is blocked after failed attempts.")
    last_login_at: datetime | None
    locale: str | None
    member_id: uuid.UUID | None
    member_name: str | None
    roles: list[UserRoleOut]
    external_roles: list[str] = Field(
        description="Roles of the identity provider seen at the last sign-in."
    )


class UserCreated(UserOut):
    temporary_password: str | None = Field(
        description="The generated first password; shown only here. Null if one was supplied."
    )


class ResetPasswordIn(ApiModel):
    new_password: str | None = Field(
        default=None,
        max_length=1000,
        description="The new temporary password. Left out, the server generates one.",
    )


class PasswordReset(ApiModel):
    temporary_password: str | None = Field(
        description="The generated temporary password; shown only here. Null if one was supplied."
    )


# --- roles -------------------------------------------------------------------------------


class RoleIn(ApiModel):
    key: RoleKey = Field(examples=["ingest"])
    name: Name
    description: Description | None = None
    permissions: list[Permission] = Field(min_length=1)


class RoleUpdate(Versioned):
    name: Name
    description: Description | None = None
    permissions: list[Permission] = Field(min_length=1)


class RoleOut(Stamped):
    key: str
    name: str
    description: str | None
    builtin: bool
    permissions: list[str]
    user_count: int = Field(description="Accounts holding the role.")
    mapping_count: int = Field(description="External roles mapped to it.")


class PermissionOut(ApiModel):
    permission: str
    description: str
    team_scoped: bool = Field(description="Whether a role can limit it to teams.")


class MappingIn(ApiModel):
    external_role: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    role_id: uuid.UUID


class MappingsIn(ApiModel):
    mappings: list[MappingIn]


class MappingOut(ApiModel):
    external_role: str
    role_id: uuid.UUID
    role_key: str
    role_name: str


class MappingsOut(ApiModel):
    provider_id: str = Field(description="The identity provider the mapping is for.")
    mappings: list[MappingOut]


# --- API tokens --------------------------------------------------------------------------

TokenStatus = Literal["active", "expired", "revoked"]


class ApiTokenIn(ApiModel):
    user_id: uuid.UUID = Field(description="The account the token acts as.")
    name: Name = Field(examples=["nightly import"])
    permissions: list[Permission] = Field(
        min_length=1,
        description="What the token may do. It can never do more than its account may.",
    )
    expires_at: datetime | None = None


class ApiTokenOut(ApiModel):
    id: uuid.UUID
    user_id: uuid.UUID
    username: str
    name: str
    prefix: str = Field(description="The first characters of the token, to recognise it.")
    permissions: list[str]
    status: TokenStatus
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


class ApiTokenCreated(ApiTokenOut):
    token: str = Field(description="The token. It is shown only in this answer.")
