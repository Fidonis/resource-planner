"""Absence source that reads a CSV file below the import directory.

Columns: ``Person`` (e-mail, user name or name; ``*`` for everybody), ``From``, ``To``, ``Type``
and optionally ``ExternalId`` (default: built from the other columns), ``StartPart``,
``EndPart``, ``Note``. Dates are ``DD.MM.YYYY`` or ``YYYY-MM-DD``.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from datetime import date
from typing import ClassVar

from pydantic import BaseModel, Field

from planner_api import csvio
from planner_api.config import Settings
from planner_api.providers import tabular
from planner_api.providers.base import (
    KIND_ABSENCE_SOURCE,
    ConfigModel,
    ExternalAbsence,
    ProviderBase,
    SourceProblem,
    TestResult,
)

PARTS = ("FULL", "AM", "PM")
REQUIRED = ("person", "from", "to", "type")


class CsvAbsenceConfig(ConfigModel):
    path: str = Field(
        title="File",
        description="Path of the CSV file below the import directory, for example absences.csv.",
        min_length=1,
        max_length=300,
    )


def derived_id(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:24]


class CsvAbsenceSource(ProviderBase):
    key: ClassVar[str] = "csv"
    kind: ClassVar[str] = KIND_ABSENCE_SOURCE
    title: ClassVar[str] = "CSV file (absences)"
    config_model: ClassVar[type[BaseModel]] = CsvAbsenceConfig

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def test_connection(self, config: BaseModel) -> TestResult:
        assert isinstance(config, CsvAbsenceConfig)
        return await asyncio.to_thread(tabular.check_file, self._settings, config.path, REQUIRED)

    async def fetch_absences(
        self, config: BaseModel, start: date, end: date
    ) -> AsyncIterator[ExternalAbsence | SourceProblem]:
        assert isinstance(config, CsvAbsenceConfig)
        rows = await asyncio.to_thread(tabular.read_rows, self._settings, config.path, REQUIRED)
        for row in rows:
            person, type_ref = row.get("person"), row.get("type")
            if not person or not type_ref:
                yield SourceProblem(f"line {row.line}", "The person or the type is empty.")
                continue
            try:
                first, last = csvio.parse_date(row.get("from")), csvio.parse_date(row.get("to"))
            except csvio.CellError as exc:
                yield SourceProblem(f"line {row.line}", str(exc))
                continue
            if last < first:
                yield SourceProblem(f"line {row.line}", "The end is before the start.")
                continue
            if last < start or first > end:
                continue  # outside the window of this run
            start_part = (row.get("startpart") or "FULL").upper()
            end_part = (row.get("endpart") or (start_part if first == last else "FULL")).upper()
            if start_part not in PARTS or end_part not in PARTS:
                yield SourceProblem(f"line {row.line}", "A part is FULL, AM or PM.")
                continue
            yield ExternalAbsence(
                external_id=row.get("externalid")
                or derived_id(person, first, last, type_ref, start_part, end_part),
                person_ref=person,
                start=first,
                end=last,
                type_ref=type_ref,
                start_part=start_part,
                end_part=end_part,
                note=row.get("note") or None,
            )
