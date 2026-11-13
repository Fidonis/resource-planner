"""Schemas of teams, skills, members and absence types."""

from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import Field, StringConstraints

from planner_api.schemas.common import ApiModel, HexColor, PersonDays, Stamped

Name = Annotated[str, StringConstraints(min_length=1, max_length=120)]
SkillKey = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._:/+#-]{0,63}$"),
]
TypeKey = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
Email = Annotated[str, StringConstraints(max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]

VERSION_DESCRIPTION = "Version being changed; an alternative to the If-Match header."


class Versioned(ApiModel):
    version: int | None = Field(default=None, description=VERSION_DESCRIPTION)


class Managed(ApiModel):
    external_source: str | None = Field(description="System that owns this record, if any.")
    external_id: str | None
    managed_fields: list[str] = Field(
        description="Fields the source system owns; they are read-only here."
    )


# --- teams -------------------------------------------------------------------------------


class TeamIn(ApiModel):
    name: Name = Field(examples=["Backend"])
    color: HexColor = "#1b5e8c"


class TeamUpdate(TeamIn, Versioned):
    pass


class TeamOut(Stamped, Managed):
    name: str
    color: str
    member_count: int
    writable: bool = Field(description="Whether the caller may change this team.")


# --- skills ------------------------------------------------------------------------------


class SkillIn(ApiModel):
    key: SkillKey = Field(
        description="Matches the label in the task source, case-insensitively.",
        examples=["Backend"],
    )
    name: Name = Field(examples=["Backend development"])


class SkillUpdate(SkillIn, Versioned):
    pass


class SkillOut(Stamped, Managed):
    key: str
    name: str
    member_count: int
    writable: bool


# --- members -----------------------------------------------------------------------------


class MemberIn(ApiModel):
    team_id: uuid.UUID
    display_name: Name = Field(examples=["Ada Lovelace"])
    email: Email | None = None
    allocation_percent: int | None = Field(
        default=None,
        ge=10,
        le=100,
        description="Share of working time available for planned tasks. "
        "Defaults to the server setting.",
    )
    active: bool = True
    note: str | None = Field(default=None, max_length=2000)


class MemberCreate(MemberIn):
    skill_ids: list[uuid.UUID] = Field(default_factory=list)


class MemberUpdate(MemberIn, Versioned):
    allocation_percent: int = Field(ge=10, le=100)


class MemberOut(Stamped, Managed):
    team_id: uuid.UUID
    team_name: str
    display_name: str
    email: str | None
    allocation_percent: int
    active: bool
    note: str | None
    user_id: uuid.UUID | None
    skill_ids: list[uuid.UUID]
    writable: bool


class MemberSkillsIn(Versioned):
    skill_ids: list[uuid.UUID]


class SkillConflict(ApiModel):
    task_id: uuid.UUID
    task_key: str
    skill_id: uuid.UUID
    skill_key: str
    assignment_count: int
    person_days: PersonDays = Field(description="Person-days still assigned to the member.")


class MemberSkillsResult(ApiModel):
    member: MemberOut
    conflicts: list[SkillConflict] = Field(
        description="Assignments that now use a skill the member no longer has (DM-02). "
        "They are kept, not deleted."
    )


# --- absence types -----------------------------------------------------------------------


class AbsenceTypeIn(ApiModel):
    key: TypeKey = Field(examples=["vacation"])
    name: Name = Field(examples=["Vacation"])
    color: HexColor = "#2a78d6"
    letter: Annotated[str, StringConstraints(min_length=1, max_length=2)] = Field(
        default="A", description="Shown next to the colour so the type never relies on colour."
    )
    reduces_capacity: bool = True
    active: bool = True
    sort_order: int = Field(default=0, ge=0, le=10000)


class AbsenceTypeUpdate(AbsenceTypeIn, Versioned):
    pass


class AbsenceTypeOut(Stamped):
    key: str
    name: str
    color: str
    letter: str
    reduces_capacity: bool
    active: bool
    sort_order: int
    absence_count: int
    writable: bool
