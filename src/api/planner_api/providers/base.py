"""The provider protocols (§9.1) and the records providers hand to the sync engine."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

KIND_TASK_SOURCE = "task_source"
KIND_DIRECTORY = "directory"
KIND_ABSENCE_SOURCE = "absence_source"
KINDS = (KIND_TASK_SOURCE, KIND_DIRECTORY, KIND_ABSENCE_SOURCE)


class ConfigModel(BaseModel):
    """Base of the settings and mapping models: camelCase on the wire, typos are refused."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        # The label of a form field when the model does not give one: "unit_factor" -> "Unit factor"
        field_title_generator=lambda name, _info: name.replace("_", " ").capitalize(),
    )


class ProviderError(Exception):
    """The source cannot be reached or refuses the credentials: the run is marked failed.

    The message is shown to the administrator, so it must not contain secrets.
    """


@dataclass(frozen=True, slots=True)
class TestResult:
    __test__ = False  # not a pytest class

    ok: bool
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SourceProblem:
    """A record the provider could not read. The run notes it and goes on (FR-INT-06)."""

    reference: str  # where it is: a line number, a key, an id
    message: str


# The categories a source may sort its states into; the mapping falls back on them for a state
# whose name it does not know (a tracker in another language, a workflow of its own).
STATUS_CATEGORIES = ("todo", "in_progress", "done")

# Planning information a source may know about a task besides its effort. They are shown with the
# task and never edited in the planner.
TASK_ATTRIBUTES = (
    "due_date",
    "start_date",
    "original_estimate_hours",
    "story_points",
    "priority",
    "assignee",
    "reporter",
)


@dataclass(frozen=True, slots=True)
class ExternalTask:
    external_id: str
    key: str
    title: str
    status: str = "open"
    description: str | None = None
    url: str | None = None
    updated_at: datetime | None = None
    labels: tuple[str, ...] = ()
    estimate: Decimal | None = None
    estimate_unit: str | None = None
    custom_fields: Mapping[str, Any] = field(default_factory=dict)
    team_hint: str | None = None
    status_category: str | None = None  # one of STATUS_CATEGORIES
    due_date: date | None = None
    start_date: date | None = None
    original_estimate_hours: Decimal | None = None
    story_points: Decimal | None = None
    priority: str | None = None
    assignee: str | None = None  # a display name
    reporter: str | None = None
    # Attributes of TASK_ATTRIBUTES the source could not read this time: they are left as they are,
    # instead of being emptied.
    unknown_attributes: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ExternalPerson:
    external_id: str
    username: str
    display_name: str
    email: str | None = None
    active: bool = True
    groups: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExternalGroup:
    external_id: str
    name: str


@dataclass(frozen=True, slots=True)
class ExternalAbsence:
    external_id: str
    person_ref: str  # e-mail, user name or external id of the person
    start: date
    end: date
    type_ref: str  # the provider's own code; the mapping turns it into an absence type
    start_part: str = "FULL"
    end_part: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class FieldOption:
    """One choice of a setting whose choices come from the source (``x-remoteOptions``)."""

    value: str
    label: str


# Markers a settings model may put on a field with ``json_schema_extra``:
# - REMOTE_OPTIONS: a list of strings whose choices the provider loads with ``field_options``.
# - SHOW_WHEN: {"field": "<alias>", "equals": "<value>"}: the form shows the field only then.
# - CONNECTION: the field decides where a secret is sent; a stored secret is not used for loading
#   choices while such a field differs from what is stored.
REMOTE_OPTIONS = "x-remoteOptions"
SHOW_WHEN = "x-showWhen"
CONNECTION = "x-connection"
# Validation context of a draft: checks that only matter for saving may be skipped.
DRAFT = "draft"


def is_draft(context: Any) -> bool:
    return isinstance(context, Mapping) and bool(context.get(DRAFT))


@runtime_checkable
class Provider(Protocol):
    """What every provider is, whatever it reads."""

    key: ClassVar[str]
    kind: ClassVar[str]
    title: ClassVar[str]
    config_model: ClassVar[type[BaseModel]]
    # Whether ``since`` narrows what the provider returns. Without it a run always sees the whole
    # source, which is what lets the engine close what has disappeared.
    supports_since: ClassVar[bool]

    def describe_config_schema(self) -> dict[str, Any]: ...

    async def test_connection(self, config: BaseModel) -> TestResult: ...


@runtime_checkable
class TaskSourceProvider(Provider, Protocol):
    def fetch_tasks(
        self, config: BaseModel, since: datetime | None
    ) -> AsyncIterator[ExternalTask | SourceProblem]: ...


@runtime_checkable
class DirectoryProvider(Provider, Protocol):
    def fetch_members(self, config: BaseModel) -> AsyncIterator[ExternalPerson | SourceProblem]: ...

    def fetch_groups(self, config: BaseModel) -> AsyncIterator[ExternalGroup]: ...


@runtime_checkable
class AbsenceSourceProvider(Provider, Protocol):
    def fetch_absences(
        self, config: BaseModel, start: date, end: date
    ) -> AsyncIterator[ExternalAbsence | SourceProblem]: ...


@runtime_checkable
class OptionSource(Protocol):
    """A provider that can list the choices of a setting marked ``x-remoteOptions``."""

    async def field_options(self, config: BaseModel, field: str) -> list[FieldOption]: ...


class ProviderBase:
    """Shared plumbing: the schema of the configuration model, with secrets marked write-only."""

    key: ClassVar[str]
    kind: ClassVar[str]
    title: ClassVar[str]
    config_model: ClassVar[type[BaseModel]]
    supports_since: ClassVar[bool] = False

    def describe_config_schema(self) -> dict[str, Any]:
        return self.config_model.model_json_schema()
