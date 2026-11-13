"""The mapping layer (FR-INT-04): how data of a source becomes data of the planner.

Everything here is configuration of one integration, stored with it. The core has no knowledge
of any tool; the strategies of §9.3 work on labels, estimates and custom fields only.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from planner_api.providers.base import ConfigModel, ExternalAbsence, ExternalPerson, ExternalTask

TWO = Decimal("0.01")
# ``be-3``, ``Backend:3`` or ``Backend=2,5``: the skill, a separator, the person-days.
DEFAULT_LABEL_PATTERN = r"^(?P<skill>.+?)\s*[-:=]\s*(?P<effort>\d+(?:[.,]\d+)?)$"
# What the usual trackers call a state, in the three states of the planner.
DEFAULT_STATUSES = {
    "open": "open",
    "new": "open",
    "to do": "open",
    "todo": "open",
    "backlog": "open",
    "in progress": "open",
    "in review": "open",
    "done": "done",
    "closed": "done",
    "resolved": "done",
    "complete": "done",
    "completed": "done",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "rejected": "cancelled",
    "won't do": "cancelled",
}

Missing = Literal["CLOSE", "KEEP"]


def quantise(value: Decimal) -> Decimal:
    return value.quantize(TWO, rounding=ROUND_HALF_UP)


class TaskMapping(ConfigModel):
    """How a task of a source becomes a task with an effort per skill (§9.3)."""

    effort_strategy: Literal["EVEN_SPLIT", "LABEL_ENCODED", "CUSTOM_FIELDS", "MANUAL"] = Field(
        default="MANUAL",
        description="EVEN_SPLIT shares the estimate over the labels that name a skill; "
        "LABEL_ENCODED reads the effort from labels such as be-3; CUSTOM_FIELDS reads one field "
        "per skill; MANUAL imports no effort.",
    )
    unit_factor: float = Field(
        default=1.0,
        gt=0,
        le=1000,
        description="Person-days per unit of the estimate (story points x factor, hours / 8 "
        "is 0.125).",
    )
    label_pattern: str = Field(
        default=DEFAULT_LABEL_PATTERN,
        max_length=300,
        description="LABEL_ENCODED: a regular expression with the groups 'skill' and 'effort'.",
    )
    label_skills: dict[str, str] = Field(
        default_factory=dict,
        description="Label to skill key, when they differ. Matching ignores case.",
    )
    unknown_labels: Literal["IGNORE", "CREATE_SKILL", "REPORT"] = Field(
        default="REPORT", description="What to do with a label that names no skill."
    )
    custom_field_skills: dict[str, str] = Field(
        default_factory=dict, description="CUSTOM_FIELDS: skill key to the name of its field."
    )
    status_map: dict[str, str] = Field(
        default_factory=dict,
        description="State of the source to open, done or cancelled. The usual names are "
        "known already.",
    )
    team_map: dict[str, str] = Field(
        default_factory=dict, description="Team hint of the source (project, component) to team."
    )
    missing: Missing = Field(
        default="CLOSE",
        description="CLOSE cancels open tasks that the source no longer returns; KEEP leaves "
        "them. Only a complete read of the source can tell.",
    )

    @field_validator("label_pattern")
    @classmethod
    def _pattern_has_its_groups(cls, value: str) -> str:
        try:
            compiled = re.compile(value)
        except re.error as exc:
            raise ValueError(f"is not a regular expression: {exc}") from exc
        if not {"skill", "effort"} <= set(compiled.groupindex):
            raise ValueError("needs the groups (?P<skill>...) and (?P<effort>...)")
        return value

    @field_validator("status_map")
    @classmethod
    def _statuses_are_known(cls, value: dict[str, str]) -> dict[str, str]:
        for target in value.values():
            if target not in ("open", "done", "cancelled"):
                raise ValueError("maps to open, done or cancelled")
        return value


class DirectoryMapping(ConfigModel):
    """Which people become members, in which team, with which skills (§9.5)."""

    include_groups: list[str] = Field(
        default_factory=list,
        description="Import only people in one of these groups. Empty: everybody.",
    )
    include_roles: list[str] = Field(
        default_factory=list,
        description="Import also people with one of these roles. Empty: no filter by role.",
    )
    group_teams: list[str] = Field(
        default_factory=list,
        description="Group=Team, one per line, the most important first. A person gets the team "
        "of the first of their groups that is listed. Without '=' the team has the group's name.",
    )
    create_teams: bool = Field(
        default=False, description="Create a team that a listed group names but that is missing."
    )
    default_team: str | None = Field(
        default=None,
        max_length=120,
        description="Team of the people without a listed group. It must exist already. Without "
        "it these people are reported and not imported.",
    )
    group_skills: list[str] = Field(
        default_factory=list,
        description="Group=Skill, one per line. People in the group get the skill added; the "
        "import never takes a skill away.",
    )
    default_allocation_percent: int | None = Field(
        default=None, ge=10, le=100, description="Share for new members. Default: the setting."
    )
    missing: Missing = Field(
        default="CLOSE",
        description="CLOSE deactivates members that the directory no longer returns; KEEP "
        "leaves them.",
    )


class AbsenceMapping(ConfigModel):
    """How an absence source's codes and people become planner data (§9.6)."""

    type_map: dict[str, str] = Field(
        default_factory=dict, description="Type code of the source to the key of an absence type."
    )
    lookback_days: int = Field(default=30, ge=0, le=3650, description="Days before today to read.")
    lookahead_days: int = Field(default=365, ge=1, le=3650, description="Days after today.")
    missing: Missing = Field(
        default="CLOSE",
        description="CLOSE removes absences of this integration that the source no longer "
        "reports within the days read; KEEP leaves them.",
    )


MAPPING_MODELS: dict[str, type[BaseModel]] = {
    "task_source": TaskMapping,
    "directory": DirectoryMapping,
    "absence_source": AbsenceMapping,
}


# --- records the applier takes ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TaskRecord:
    external_id: str
    key: str
    title: str
    status: str = "open"
    description: str | None = None
    url: str | None = None
    updated_at: datetime | None = None
    owner_team: str | None = None
    # Skill key to person-days; None leaves the efforts of the task alone.
    efforts: Mapping[str, Decimal] | None = None
    # What the source keeps owning: the fields a later sync may overwrite.
    owned: tuple[str, ...] = ("title", "status", "efforts")
    create_skills: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MemberRecord:
    external_id: str
    display_name: str
    email: str | None = None
    active: bool = True
    team: str | None = None
    team_is_default: bool = False
    create_team: bool = False
    skills: tuple[str, ...] = ()
    allocation_percent: int | None = None


@dataclass(frozen=True, slots=True)
class AbsenceRecord:
    external_id: str
    person_ref: str
    type_key: str
    start: date
    end: date
    start_part: str = "FULL"
    end_part: str = "FULL"
    note: str | None = None


# --- tasks --------------------------------------------------------------------------------


@dataclass
class EffortResult:
    efforts: dict[str, Decimal] | None
    create_skills: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def map_status(raw: str, mapping: TaskMapping) -> str:
    wanted = raw.strip().lower()
    custom = {k.strip().lower(): v for k, v in mapping.status_map.items()}
    return custom.get(wanted) or DEFAULT_STATUSES.get(wanted, "open")


def _skill_for_label(label: str, mapping: TaskMapping) -> str:
    renamed = {k.strip().lower(): v for k, v in mapping.label_skills.items()}
    return renamed.get(label.strip().lower(), label.strip())


def _decimal(text: object) -> Decimal | None:
    try:
        value = Decimal(str(text).strip().replace(",", "."))
    except (ArithmeticError, ValueError):
        return None
    return value if value.is_finite() and value >= 0 else None


def map_efforts(
    task: ExternalTask, mapping: TaskMapping, known: Callable[[str], str | None]
) -> EffortResult:
    """The effort per skill (skill key to person-days) of one task.

    ``known`` returns the key a skill has in the planner (or ``None``).
    """
    strategy = mapping.effort_strategy
    if strategy == "MANUAL":
        return EffortResult(None)
    factor = Decimal(str(mapping.unit_factor))
    result = EffortResult(None)
    efforts: dict[str, Decimal] = {}

    def add(label: str, days: Decimal, *, source: str) -> None:
        named = _skill_for_label(label, mapping)
        key = known(named)
        if key is None:
            if mapping.unknown_labels == "CREATE_SKILL":
                key = named
                if named.lower() not in {s.lower() for s in result.create_skills}:
                    result.create_skills.append(named)
            elif mapping.unknown_labels == "REPORT":
                result.notes.append(f"{source} '{label}' names no skill.")
                return
            else:
                return
        if days > 0:
            efforts[key] = efforts.get(key, Decimal(0)) + days

    if strategy == "LABEL_ENCODED":
        pattern = re.compile(mapping.label_pattern)
        for label in task.labels:
            found = pattern.match(label.strip())
            amount = _decimal(found["effort"]) if found else None
            if found is None or amount is None:
                continue  # an ordinary label
            add(found["skill"], amount * factor, source="The label")
    elif strategy == "CUSTOM_FIELDS":
        fields = {_normal(k): v for k, v in task.custom_fields.items()}
        for skill, field_name in mapping.custom_field_skills.items():
            amount = _decimal(fields.get(_normal(field_name), ""))
            if amount is not None:
                add(skill, amount * factor, source="The field")
    else:  # EVEN_SPLIT
        total = (task.estimate or Decimal(0)) * factor
        named: list[str] = []
        for label in task.labels:
            if known(_skill_for_label(label, mapping)) or mapping.unknown_labels == "CREATE_SKILL":
                named.append(label)
            elif mapping.unknown_labels == "REPORT":
                result.notes.append(f"The label '{label}' names no skill.")
        if total > 0 and named:
            share = quantise(total / len(named))
            rest = quantise(total) - share * len(named)
            for index, label in enumerate(named):
                add(label, share + (rest if index == 0 else Decimal(0)), source="The label")
        elif total > 0:
            result.notes.append("The estimate cannot be split: no label names a skill.")

    result.efforts = {k: quantise(v) for k, v in efforts.items() if quantise(v) > 0}
    return result


def _normal(name: str) -> str:
    return re.sub(r"[\s_\-]+", "", name).lower()


def map_task(
    task: ExternalTask, mapping: TaskMapping, known: Callable[[str], str | None]
) -> TaskRecord:
    effort = map_efforts(task, mapping, known)
    hint = (task.team_hint or "").strip()
    teams = {k.strip().lower(): v for k, v in mapping.team_map.items()}
    owned = ("title", "status") if effort.efforts is None else ("title", "status", "efforts")
    return TaskRecord(
        external_id=task.external_id,
        key=task.key,
        title=task.title,
        status=map_status(task.status, mapping),
        description=task.description,
        url=task.url,
        updated_at=task.updated_at,
        owner_team=teams.get(hint.lower()) if hint else None,
        efforts=effort.efforts,
        owned=owned,
        create_skills=tuple(effort.create_skills),
        notes=tuple(effort.notes),
    )


# --- people -------------------------------------------------------------------------------


def _pairs(lines: list[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for line in lines:
        left, sep, right = line.partition("=")
        if left.strip():
            pairs.append((left.strip(), right.strip() if sep and right.strip() else left.strip()))
    return pairs


def _configured(group: str) -> str:
    """A configured group: a bare name, or a full path such as ``/Engineering/Backend``."""
    parts = [part for part in group.strip().lower().split("/") if part]
    if len(parts) == 1 and not group.strip().startswith("/"):
        return parts[0]
    return "/" + "/".join(parts)


def _known(groups: tuple[str, ...]) -> set[str]:
    """What a person's groups count as: each path, its ancestors, and every name on the way."""
    names: set[str] = set()
    for group in groups:
        parts = [part for part in group.strip().lower().split("/") if part]
        for index in range(1, len(parts) + 1):
            names.add("/" + "/".join(parts[:index]))
            names.add(parts[index - 1])
    return names


def wanted(person: ExternalPerson, mapping: DirectoryMapping) -> bool:
    """Whether the person is in scope of the import (FR-DIR-01)."""
    if not (mapping.include_groups or mapping.include_roles):
        return True
    groups = _known(person.groups)
    if any(_configured(g) in groups for g in mapping.include_groups):
        return True
    roles = {r.lower() for r in person.roles}
    return any(r.lower() in roles for r in mapping.include_roles)


def map_person(person: ExternalPerson, mapping: DirectoryMapping) -> MemberRecord:
    groups = _known(person.groups)
    team: str | None = None
    for group, target in _pairs(mapping.group_teams):  # the order is the priority
        if _configured(group) in groups:
            team = target
            break
    skills = tuple(
        skill for group, skill in _pairs(mapping.group_skills) if _configured(group) in groups
    )
    return MemberRecord(
        external_id=person.external_id,
        display_name=person.display_name or person.username,
        email=person.email,
        active=person.active,
        team=team or mapping.default_team,
        team_is_default=team is None and mapping.default_team is not None,
        create_team=mapping.create_teams and team is not None,
        skills=skills,
        allocation_percent=mapping.default_allocation_percent,
    )


# --- absences -----------------------------------------------------------------------------


class UnmappedTypeError(Exception):
    pass


def map_absence(absence: ExternalAbsence, mapping: AbsenceMapping) -> AbsenceRecord:
    types = {k.strip().lower(): v for k, v in mapping.type_map.items()}
    key = types.get(absence.type_ref.strip().lower())
    if key is None:
        raise UnmappedTypeError(f"The type '{absence.type_ref}' is not in the type mapping.")
    return AbsenceRecord(
        external_id=absence.external_id,
        person_ref=absence.person_ref,
        type_key=key,
        start=absence.start,
        end=absence.end,
        start_part=absence.start_part,
        end_part=absence.end_part
        or ("FULL" if absence.start != absence.end else absence.start_part),
        note=absence.note,
    )
