"""Helpers for the sync tests: a provider that comes from outside the package, files, runs."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel
from tests.api.support import Caller

from planner_api.providers.base import (
    KIND_TASK_SOURCE,
    ExternalTask,
    ProviderBase,
    SourceProblem,
    TestResult,
)

INTEGRATIONS = "/api/v1/integrations"


class FakeConfig(BaseModel):
    count: int = 2


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

    async def fetch_tasks(
        self, config: BaseModel, since: datetime | None
    ) -> AsyncIterator[ExternalTask | SourceProblem]:
        assert isinstance(config, FakeConfig)
        for number in range(config.count):
            yield ExternalTask(
                external_id=f"F-{number}",
                key=f"FAKE-{number}",
                title=f"Fake task {number}",
                estimate=Decimal(4),
                labels=("Backend",),
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
