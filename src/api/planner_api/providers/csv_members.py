"""Directory that reads a CSV file below the import directory.

Columns: ``Name`` and optionally ``ExternalId`` (default: the e-mail or the user name),
``Username``, ``Email``, ``Active`` (yes/no, default yes), ``Groups`` and ``Roles`` (separated
by ``|`` or ``,``).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import ClassVar

from pydantic import BaseModel, Field

from planner_api.config import Settings
from planner_api.providers import tabular
from planner_api.providers.base import (
    KIND_DIRECTORY,
    ConfigModel,
    ExternalGroup,
    ExternalPerson,
    ProviderBase,
    SourceProblem,
    TestResult,
)

FALSE = {"0", "false", "no", "n", "nein", "inactive", "off"}


class CsvDirectoryConfig(ConfigModel):
    path: str = Field(
        title="File",
        description="Path of the CSV file below the import directory, for example people.csv.",
        min_length=1,
        max_length=300,
    )


class CsvDirectory(ProviderBase):
    key: ClassVar[str] = "csv"
    kind: ClassVar[str] = KIND_DIRECTORY
    title: ClassVar[str] = "CSV file (people)"
    config_model: ClassVar[type[BaseModel]] = CsvDirectoryConfig

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def test_connection(self, config: BaseModel) -> TestResult:
        assert isinstance(config, CsvDirectoryConfig)
        return await asyncio.to_thread(tabular.check_file, self._settings, config.path, ("name",))

    async def fetch_members(
        self, config: BaseModel
    ) -> AsyncIterator[ExternalPerson | SourceProblem]:
        assert isinstance(config, CsvDirectoryConfig)
        rows = await asyncio.to_thread(tabular.read_rows, self._settings, config.path, ("name",))
        for row in rows:
            name = row.get("name")
            username = row.get("username") or row.get("email") or name
            identity = row.get("externalid") or row.get("email") or username
            if not name or not identity:
                yield SourceProblem(f"line {row.line}", "The name is empty.")
                continue
            yield ExternalPerson(
                external_id=identity,
                username=username,
                display_name=name,
                email=row.get("email") or None,
                active=row.get("active").lower() not in FALSE,
                groups=tabular.split_list(row.get("groups")),
                roles=tabular.split_list(row.get("roles")),
            )

    async def fetch_groups(self, config: BaseModel) -> AsyncIterator[ExternalGroup]:
        assert isinstance(config, CsvDirectoryConfig)
        seen: dict[str, ExternalGroup] = {}
        async for person in self.fetch_members(config):
            if isinstance(person, SourceProblem):
                continue
            for name in person.groups:
                seen.setdefault(name.lower(), ExternalGroup(name, name))
        for found in seen.values():
            yield found
