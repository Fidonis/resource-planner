"""Schemas of integrations, sync runs and the machine ingest API."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints

from planner_api.schemas.common import Amount, ApiModel, PersonDays, Stamped
from planner_api.schemas.org import Email, SkillKey, Versioned

IntegrationType = Literal["task_source", "directory", "absence_source"]
Name = Annotated[str, StringConstraints(min_length=1, max_length=160)]
Source = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$"),
    Field(
        description="Name of the sending system, for example 'jira-eu' or 'hr-export'. Records "
        "are matched by this name and their external id."
    ),
]


class ProviderInfo(ApiModel):
    key: str
    type: IntegrationType
    title: str
    supports_since: bool = Field(description="Whether the provider can read only what changed.")
    config_schema: dict[str, Any] = Field(
        description="JSON Schema of the provider's settings. Fields marked writeOnly are secrets."
    )
    mapping_schema: dict[str, Any] = Field(description="JSON Schema of the mapping for the type.")


class ProviderList(ApiModel):
    items: list[ProviderInfo]


class IntegrationIn(ApiModel):
    name: Name
    type: IntegrationType
    provider_key: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    enabled: bool = False
    schedule: str | None = Field(
        default=None,
        max_length=120,
        description="A cron line of five fields (UTC), for example '0 6 * * 1-5'. Empty: manual "
        "runs only.",
    )
    config: dict[str, Any] = Field(
        default_factory=dict, description="Settings of the provider, see the provider's schema."
    )
    mapping: dict[str, Any] = Field(
        default_factory=dict, description="How the data is mapped, see the mapping schema."
    )


class IntegrationUpdate(Versioned):
    name: Name
    enabled: bool = False
    schedule: str | None = Field(default=None, max_length=120)
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Settings of the provider. A secret that is left out or empty stays as it is.",
    )
    mapping: dict[str, Any] = Field(default_factory=dict)


class IntegrationOut(Stamped):
    name: str
    type: IntegrationType
    provider_key: str
    provider_title: str | None = Field(description="Empty when the provider is not installed.")
    enabled: bool
    schedule: str | None
    config: dict[str, Any] = Field(description="The settings without their secrets.")
    secrets_set: list[str] = Field(description="Names of the secret settings that have a value.")
    mapping: dict[str, Any]
    last_sync_at: datetime | None
    last_sync_status: str | None


class ConnectionResult(ApiModel):
    ok: bool
    message: str


class LogEntryOut(ApiModel):
    level: Literal["info", "warning", "error"]
    message: str
    reference: str | None = None


class SyncRunOut(ApiModel):
    id: uuid.UUID
    integration_id: uuid.UUID
    started_at: datetime
    finished_at: datetime | None
    status: str = Field(description="running, success, partial, failed or aborted.")
    dry_run: bool
    full: bool
    created: int
    updated: int
    skipped: int
    errors: int
    triggered_by: str | None


class SyncRunDetail(SyncRunOut):
    log: list[LogEntryOut]


# --- ingest ---------------------------------------------------------------------------------


class IngestEffort(ApiModel):
    skill: SkillKey = Field(description="Key of the skill.")
    person_days: PersonDays = Field(gt=0, le=9999.99)


class IngestTask(ApiModel):
    external_id: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    key: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    title: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    description: str | None = None
    status: Literal["open", "done", "cancelled"] = "open"
    url: Annotated[str, StringConstraints(max_length=1000)] | None = None
    updated_at: datetime | None = None
    team: Annotated[str, StringConstraints(max_length=120)] | None = Field(
        default=None, description="Name of the owner team; unknown names are ignored."
    )
    efforts: list[IngestEffort] | None = Field(
        default=None,
        description="Effort per skill. Left out, the efforts of the task stay as they are.",
    )
    due_date: date | None = Field(
        default=None,
        description="Due date. This and the other planning attributes below are shown with the "
        "task and cannot be edited in the planner; one that is left out stays as it is, null "
        "empties it.",
        examples=["2026-12-18"],
    )
    start_date: date | None = Field(default=None, examples=["2026-12-01"])
    original_estimate_hours: Amount | None = Field(
        default=None, ge=0, le=999999.99, description="Time estimate in hours.", examples=[16]
    )
    story_points: Amount | None = Field(default=None, ge=0, le=999999.99, examples=[5])
    priority: Annotated[str, StringConstraints(max_length=64)] | None = Field(
        default=None, examples=["High"]
    )
    assignee: Annotated[str, StringConstraints(max_length=160)] | None = Field(
        default=None, description="Name of the assigned person.", examples=["Alex Example"]
    )
    reporter: Annotated[str, StringConstraints(max_length=160)] | None = Field(
        default=None,
        description="Name of the person who created the task.",
        examples=["Sam Example"],
    )


class IngestTasksIn(ApiModel):
    source: Source
    tasks: list[IngestTask] = Field(min_length=1, max_length=2000)
    create_skills: bool = Field(default=False, description="Create skills that do not exist yet.")


class IngestMember(ApiModel):
    external_id: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    email: Email | None = None
    active: bool = True
    team: Annotated[str, StringConstraints(max_length=120)] | None = None
    skills: list[SkillKey] = Field(default_factory=list, description="Skills to add.")


class IngestMembersIn(ApiModel):
    source: Source
    members: list[IngestMember] = Field(min_length=1, max_length=2000)
    create_teams: bool = False
    default_team: Annotated[str, StringConstraints(max_length=120)] | None = Field(
        default=None, description="Team for members without one."
    )


class IngestAbsence(ApiModel):
    external_id: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    person: Annotated[str, StringConstraints(min_length=1, max_length=255)] = Field(
        description="E-mail, name, user name or external id of the member; '*' for everybody."
    )
    type: Annotated[str, StringConstraints(min_length=1, max_length=64)] = Field(
        description="Key of an absence type."
    )
    start_date: date
    end_date: date
    start_part: Literal["FULL", "AM", "PM"] = "FULL"
    end_part: Literal["FULL", "AM", "PM"] | None = None
    note: str | None = None


class IngestAbsencesIn(ApiModel):
    source: Source
    absences: list[IngestAbsence] = Field(min_length=1, max_length=5000)


class IngestResult(ApiModel):
    dry_run: bool
    created: int
    updated: int
    skipped: int
    errors: int
    messages: list[LogEntryOut]
