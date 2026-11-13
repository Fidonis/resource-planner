"""Schemas of tasks, assignments, absences and the planning read models."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from planner_api.schemas.common import Amount, ApiModel, PersonDays, Stamped
from planner_api.schemas.org import Managed, Versioned

TaskStatus = Literal["open", "done", "cancelled"]
AbsencePart = Literal["FULL", "AM", "PM"]
SkillStatus = Literal["OK", "TIGHT", "BOTTLENECK"]

TaskKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/#_-]{0,63}$")]
Title = Annotated[str, StringConstraints(min_length=1, max_length=300)]
PositiveDays = Annotated[
    PersonDays,
    Field(gt=0, max_digits=6, decimal_places=2, description="Person-days, two decimals."),
]


# --- tasks -------------------------------------------------------------------------------


class EffortIn(ApiModel):
    skill_id: uuid.UUID
    person_days: PositiveDays


class TaskIn(ApiModel):
    key: TaskKey = Field(description="Human key, unique among manual tasks.", examples=["SHOP-101"])
    title: Title = Field(examples=["Checkout redesign"])
    description: str | None = Field(default=None, max_length=20000)
    owner_team_id: uuid.UUID | None = Field(
        default=None, description="Informational: a task may need skills from any team."
    )
    status: TaskStatus = "open"


class TaskCreate(TaskIn):
    efforts: list[EffortIn] = Field(default_factory=list, description="Effort per skill.")


class TaskUpdate(TaskIn, Versioned):
    efforts: list[EffortIn] | None = Field(
        default=None,
        description="Replaces the efforts when sent; omit it to leave them as they are. "
        "Efforts below what is already assigned are refused unless `force=true` (DM-03).",
    )
    locked_fields: list[str] | None = Field(
        default=None,
        description="Fields the source system still owns. Only fields can be removed from the "
        "list (unlocked), and only with `integration:admin`.",
    )


class EffortsIn(Versioned):
    efforts: list[EffortIn]


class EffortOut(ApiModel):
    skill_id: uuid.UUID
    skill_key: str
    skill_name: str
    person_days: PersonDays
    assigned: PersonDays
    open: PersonDays = Field(description="Effort minus assigned; negative when over-planned.")
    overplanned: bool = Field(description="More is assigned than the effort asks for (DM-06).")


class TaskOut(Stamped, Managed):
    key: str
    title: str
    description: str | None
    owner_team_id: uuid.UUID | None
    owner_team_name: str | None
    status: TaskStatus
    external_url: str | None = Field(description="Where the task is in its source system.")
    external_updated_at: datetime | None = Field(
        description="When the source system last changed the task."
    )
    source_name: str | None = Field(
        description="Name of the integration that imported the task, or the source that sent it "
        "through the ingest API.",
        examples=["Jira"],
    )
    due_date: date | None = Field(
        description="Due date in the source system. Like the other planning attributes below it "
        "is written by the source only and empty for manual tasks.",
        examples=["2026-12-18"],
    )
    start_date: date | None = Field(
        description="Planned start in the source system.", examples=["2026-12-01"]
    )
    original_estimate_hours: Amount | None = Field(
        description="Original time estimate of the source system, in hours.", examples=[16]
    )
    story_points: Amount | None = Field(description="Story points of the source.", examples=[5])
    priority: str | None = Field(description="Priority as the source names it.", examples=["High"])
    assignee: str | None = Field(
        description="Name of the person the source has assigned the task to.",
        examples=["Alex Example"],
    )
    reporter: str | None = Field(
        description="Name of the person who created the task in the source.",
        examples=["Sam Example"],
    )
    locked_fields: list[str]
    efforts: list[EffortOut]
    total_effort: PersonDays
    total_assigned: PersonDays
    total_open: PersonDays = Field(description="Effort still to plan; never negative.")
    planned_percent: float = Field(description="Share of the effort that is assigned, 0 to 100.")
    overplanned: bool
    writable: bool


# --- assignments -------------------------------------------------------------------------


class AssignmentIn(ApiModel):
    task_id: uuid.UUID
    skill_id: uuid.UUID
    member_id: uuid.UUID
    week_start: date = Field(description="Monday of the week.", examples=["2026-03-02"])
    person_days: PositiveDays


class AssignmentUpdate(Versioned):
    member_id: uuid.UUID
    week_start: date
    person_days: PositiveDays


class MemberWeekFigures(ApiModel):
    week_start: date
    present_days: PersonDays
    absent_days: PersonDays
    capacity: PersonDays
    booked: PersonDays
    free: PersonDays = Field(description="Capacity minus booked; negative when overbooked.")
    overbooked: bool
    full: bool = Field(description="Booked equals the capacity (and there is capacity).")


class MemberWeekOut(MemberWeekFigures):
    member_id: uuid.UUID


class TaskSkillOut(ApiModel):
    task_id: uuid.UUID
    skill_id: uuid.UUID
    effort: PersonDays
    assigned: PersonDays
    open: PersonDays
    overplanned: bool


class AssignmentOut(Stamped):
    task_id: uuid.UUID
    task_key: str
    task_title: str
    skill_id: uuid.UUID
    skill_key: str
    member_id: uuid.UUID
    member_name: str
    team_id: uuid.UUID
    week_start: date
    person_days: PersonDays
    closed: bool = Field(description="The task is done or cancelled; its assignments are history.")
    writable: bool


class AssignmentEffect(ApiModel):
    """What a change does, whether it was kept or only tried with ``dryRun``."""

    dry_run: bool
    overbooked: bool = Field(description="Any member-week touched by the change is overbooked.")
    overplanned: bool = Field(description="The task skill is assigned more than its effort.")
    merged_into: uuid.UUID | None = Field(
        default=None,
        description="The change was added to this existing assignment of the same task, skill, "
        "member and week.",
    )
    deleted_id: uuid.UUID | None = Field(
        default=None, description="The assignment that no longer exists after the change."
    )
    member_weeks: list[MemberWeekOut] = Field(
        description="Capacity figures of every member-week the change touched, target first."
    )
    task_skill: TaskSkillOut


class AssignmentResult(AssignmentOut, AssignmentEffect):
    pass


# --- absences ----------------------------------------------------------------------------


class AbsenceIn(ApiModel):
    member_id: uuid.UUID | None = Field(
        default=None, description="Empty applies to all members (a company holiday)."
    )
    absence_type_id: uuid.UUID
    start_date: date
    end_date: date = Field(description="Inclusive.")
    start_part: AbsencePart = Field(default="FULL", description="Which part of the first day.")
    end_part: AbsencePart = Field(default="FULL", description="Which part of the last day.")
    note: str | None = Field(default=None, max_length=1000)


class AbsenceUpdate(AbsenceIn, Versioned):
    pass


class AbsenceOut(Stamped, Managed):
    member_id: uuid.UUID | None
    member_name: str | None
    team_id: uuid.UUID | None
    absence_type_id: uuid.UUID
    type_key: str
    type_name: str
    type_color: str
    type_letter: str
    reduces_capacity: bool
    start_date: date
    end_date: date
    start_part: AbsencePart
    end_part: AbsencePart
    note: str | None
    working_days: PersonDays = Field(description="Working days covered; a half day counts 0.5.")
    writable: bool


class AbsenceEffect(ApiModel):
    dry_run: bool
    overbooked_member_weeks: list[MemberWeekOut] = Field(
        description="Member-weeks in the span that are overbooked once the change applies."
    )


class AbsenceResult(AbsenceOut, AbsenceEffect):
    pass


class AbsenceDeleteResult(AbsenceEffect):
    deleted_id: uuid.UUID


class CalendarSlot(ApiModel):
    absence_id: uuid.UUID
    type_id: uuid.UUID
    letter: str
    color: str
    reduces_capacity: bool


class CalendarDay(ApiModel):
    date: date
    am: CalendarSlot | None
    pm: CalendarSlot | None


class CalendarMember(ApiModel):
    member_id: uuid.UUID
    display_name: str
    team_id: uuid.UUID
    days: list[CalendarDay]


class WeekOut(ApiModel):
    week_start: date
    iso_week: int
    iso_year: int


class CalendarOut(ApiModel):
    start: date
    weeks: list[WeekOut]
    days: list[date] = Field(description="The working days of the horizon, in order.")
    members: list[CalendarMember]


# --- the planning board ------------------------------------------------------------------


class SkillRef(ApiModel):
    id: uuid.UUID
    key: str
    name: str


class ChipOut(ApiModel):
    id: uuid.UUID
    version: int
    task_id: uuid.UUID
    task_key: str
    task_title: str
    skill_id: uuid.UUID
    skill_key: str
    person_days: PersonDays
    closed: bool


class MemberWeekPlan(MemberWeekFigures):
    assignments: list[ChipOut]


class MemberPlan(ApiModel):
    id: uuid.UUID
    display_name: str
    team_id: uuid.UUID
    allocation_percent: int
    active: bool
    skill_ids: list[uuid.UUID]
    writable: bool = Field(description="The caller may assign work to this member.")
    weeks: list[MemberWeekPlan]
    capacity: PersonDays
    booked: PersonDays
    free: PersonDays


class TeamPlan(ApiModel):
    id: uuid.UUID
    name: str
    color: str
    capacity: PersonDays
    booked: PersonDays
    free: PersonDays
    members: list[MemberPlan]


class PoolEffort(ApiModel):
    skill_id: uuid.UUID
    skill_key: str
    effort: PersonDays
    assigned: PersonDays
    open: PersonDays
    overplanned: bool
    done: bool = Field(description="Fully planned: shown as done and not offered for dragging.")


class PoolTask(ApiModel):
    id: uuid.UUID
    version: int
    key: str
    title: str
    status: TaskStatus
    owner_team_id: uuid.UUID | None
    efforts: list[PoolEffort]
    total_effort: PersonDays
    total_open: PersonDays
    planned_percent: float


class PlanningOut(ApiModel):
    start: date = Field(description="Monday of the first week.")
    weeks: list[WeekOut]
    skills: list[SkillRef] = Field(description="Every skill the response refers to.")
    teams: list[TeamPlan]
    tasks: list[PoolTask]
    can_assign: bool = Field(description="The caller may create assignments at all.")


class AvailabilityMember(ApiModel):
    member_id: uuid.UUID
    display_name: str
    team_id: uuid.UUID
    team_name: str
    allocation_percent: int
    weeks: list[MemberWeekFigures]
    total_capacity: PersonDays
    total_booked: PersonDays
    total_free: PersonDays


class AvailabilityOut(ApiModel):
    skill_id: uuid.UUID
    skill_key: str
    start: date
    weeks: list[WeekOut]
    members: list[AvailabilityMember] = Field(description="Most free capacity first.")


# --- overview ----------------------------------------------------------------------------


class KpiOut(ApiModel):
    demand: PersonDays
    assigned: PersonDays
    assigned_percent: float
    open: PersonDays
    overbooked_member_weeks: int


class SkillRowOut(ApiModel):
    skill_id: uuid.UUID
    skill_key: str
    skill_name: str
    demand: PersonDays
    assigned: PersonDays
    assigned_percent: float
    open: PersonDays
    pool: PersonDays
    pool_free: PersonDays
    status: SkillStatus


class TeamRowOut(ApiModel):
    team_id: uuid.UUID
    team_name: str
    color: str
    member_count: int
    capacity: PersonDays
    booked: PersonDays
    free: PersonDays
    utilisation_percent: float


class OverviewOut(ApiModel):
    start: date
    weeks: list[WeekOut]
    kpis: KpiOut
    skills: list[SkillRowOut]
    teams: list[TeamRowOut]


# --- imports and demo data ---------------------------------------------------------------


class ImportLineError(ApiModel):
    line: int = Field(description="Line of the CSV file, counting the header as line 1.")
    field: str | None
    message: str
    code: str


class ImportResult(ApiModel):
    dry_run: bool
    created: int
    updated: int
    skipped: int = Field(description="Rows that changed nothing.")
    errors: list[ImportLineError]
    unknown_skills: list[str] = Field(description="Skills the file names that do not exist.")
    created_skills: list[str]


class DemoSeedResult(ApiModel):
    teams: int
    skills: int
    members: int
    tasks: int
    assignments: int
    absences: int
