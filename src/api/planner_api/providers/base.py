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


class ProviderBase:
    """Shared plumbing: the schema of the configuration model, with secrets marked write-only."""

    key: ClassVar[str]
    kind: ClassVar[str]
    title: ClassVar[str]
    config_model: ClassVar[type[BaseModel]]
    supports_since: ClassVar[bool] = False

    def describe_config_schema(self) -> dict[str, Any]:
        return self.config_model.model_json_schema()
