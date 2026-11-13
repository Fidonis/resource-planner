"""Helpers for the sync tests: a provider that comes from outside the package, files, runs."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, ValidationInfo, model_validator
from tests.api.support import Caller

from planner_api.providers.base import (
    CONNECTION,
    KIND_TASK_SOURCE,
    REMOTE_OPTIONS,
    SHOW_WHEN,
    ExternalTask,
    FieldOption,
    ProviderBase,
    ProviderError,
    SourceProblem,
    TestResult,
    is_draft,
)

INTEGRATIONS = "/api/v1/integrations"


class FakeConfig(BaseModel):
    count: int = 2
    attributes: bool = False  # hand over planning attributes, some of them awkward
    address: str = Field(default="fake://tracker", json_schema_extra={CONNECTION: True})
    token: str | None = Field(default=None, json_schema_extra={"writeOnly": True})
    scope: Literal["ALL", "SOME"] = "ALL"
    choices: list[str] = Field(
        default_factory=list,
        json_schema_extra={REMOTE_OPTIONS: True, SHOW_WHEN: {"field": "scope", "equals": "SOME"}},
    )

    @model_validator(mode="after")
    def _some_needs_a_choice(self, info: ValidationInfo) -> FakeConfig:
        if self.scope == "SOME" and not self.choices and not is_draft(info.context):
            raise ValueError("SOME needs at least one choice")
        return self


class FakeTaskSource(ProviderBase):
    """What somebody else's package would register in the entry-point group."""

    key: ClassVar[str] = "fake"
    kind: ClassVar[str] = KIND_TASK_SOURCE
    title: ClassVar[str] = "Fake tracker"
    config_model: ClassVar[type[BaseModel]] = FakeConfig

    def __init__(self, settings: object) -> None:
        self._settings = settings

    async def test_connection(self, config: BaseModel) -> TestResult:
        return TestResult(True, "The fake tracker is always there.")

    async def field_options(self, config: BaseModel, field: str) -> list[FieldOption]:
        assert isinstance(config, FakeConfig) and field == "choices"
        if config.address == "fake://down":
            raise ProviderError("The fake tracker is down.")
        signed = " (signed in)" if config.token else ""
        return [FieldOption(f"C{n}", f"Choice {n}{signed}") for n in range(config.count)]

    async def fetch_tasks(
        self, config: BaseModel, since: datetime | None
    ) -> AsyncIterator[ExternalTask | SourceProblem]:
        assert isinstance(config, FakeConfig)
        for number in range(config.count):
            extra: dict[str, Any] = {}
            if config.attributes:
                extra = {
                    "due_date": date(2026, 12, 18),
                    "original_estimate_hours": Decimal(1) / Decimal(3),  # stored as 0.33
                    "story_points": Decimal(10_000_000),  # does not fit the column
                    "assignee": "A" * 200,  # longer than the column
                    "description": "x" * 25_000,
                    "unknown_attributes": frozenset({"priority"}) if number else frozenset(),
                    "priority": "High",
                }
            yield ExternalTask(
                external_id=f"F-{number}",
                key=f"FAKE-{number}",
                title=f"Fake task {number}",
                estimate=Decimal(4),
                labels=("Backend",),
                **extra,
            )


def write(imports: Path, name: str, text: str) -> str:
    """A file in the import directory; returns the relative path a provider is told."""
    (imports / name).write_text(text, encoding="utf-8", newline="")
    return name


async def integration(
    admin: Caller,
    kind: str,
    path: str,
    *,
    name: str = "Import",
    provider: str = "csv",
    mapping: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    body = {
        "name": name,
        "type": kind,
        "providerKey": provider,
        "config": {"path": path},
        "mapping": mapping or {},
        "enabled": True,
        **extra,
    }
    return await admin.create(INTEGRATIONS, body)


async def sync(
    admin: Caller, integration_id: str, expect: int = 200, **params: Any
) -> dict[str, Any]:
    response = await admin.post(f"{INTEGRATIONS}/{integration_id}/sync", params=params or None)
    assert response.status_code == expect, response.text
    body: dict[str, Any] = response.json()
    return body


def counts(run: dict[str, Any]) -> tuple[int, int, int, int]:
    return run["created"], run["updated"], run["skipped"], run["errors"]
