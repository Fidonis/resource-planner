"""Task source that reads a CSV file below the import directory.

Columns (the order does not matter, names ignore case, spaces and underscores): ``Key``,
``Title`` and optionally ``ExternalId`` (default: the key), ``Description``, ``Status``, ``Url``,
``UpdatedAt``, ``Labels`` (separated by ``|``), ``Estimate``, ``EstimateUnit``, ``Team``.
Every other column is handed to the mapping as a custom field.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import ClassVar

from pydantic import BaseModel, Field

from planner_api import csvio
from planner_api.config import Settings
from planner_api.providers import tabular
from planner_api.providers.base import (
    KIND_TASK_SOURCE,
    ConfigModel,
    ExternalTask,
    ProviderBase,
    SourceProblem,
    TestResult,
)

KNOWN = {
    "externalid",
    "key",
    "title",
    "description",
    "status",
    "url",
    "updatedat",
    "labels",
    "estimate",
    "estimateunit",
    "unit",
    "team",
}


class CsvTaskConfig(ConfigModel):
    path: str = Field(
        title="File",
        description="Path of the CSV file below the import directory, for example tasks.csv.",
        min_length=1,
        max_length=300,
    )


def lenient_decimal(text: str) -> Decimal:
    try:
        value = Decimal(text.strip().replace(",", "."))
    except InvalidOperation as exc:
        raise csvio.CellError(f"'{text}' is not a number.") from exc
    if not value.is_finite() or value < 0:
        raise csvio.CellError(f"'{text}' is not a number of zero or more.")
    return value


def parse_moment(text: str) -> datetime:
    """An ISO date-time or one of the date formats of the manual import."""
    try:
        moment = datetime.fromisoformat(text.strip())
    except ValueError:
        moment = datetime.combine(csvio.parse_date(text), datetime.min.time())
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


class CsvTaskSource(ProviderBase):
    key: ClassVar[str] = "csv"
    kind: ClassVar[str] = KIND_TASK_SOURCE
    title: ClassVar[str] = "CSV file (tasks)"
    config_model: ClassVar[type[BaseModel]] = CsvTaskConfig

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def test_connection(self, config: BaseModel) -> TestResult:
        assert isinstance(config, CsvTaskConfig)
        return await asyncio.to_thread(
            tabular.check_file, self._settings, config.path, ("key", "title")
        )

    async def fetch_tasks(
        self, config: BaseModel, since: datetime | None
    ) -> AsyncIterator[ExternalTask | SourceProblem]:
        assert isinstance(config, CsvTaskConfig)
        rows = await asyncio.to_thread(
            tabular.read_rows, self._settings, config.path, ("key", "title")
        )
        for row in rows:
            key = row.get("key")
            if not key or not row.get("title"):
                yield SourceProblem(f"line {row.line}", "The key or the title is empty.")
                continue
            try:
                estimate = lenient_decimal(row.get("estimate")) if row.get("estimate") else None
                updated = parse_moment(row.get("updatedat")) if row.get("updatedat") else None
            except csvio.CellError as exc:
                yield SourceProblem(f"line {row.line} ({key})", str(exc))
                continue
            yield ExternalTask(
                external_id=row.get("externalid") or key,
                key=key,
                title=row.get("title"),
                status=row.get("status") or "open",
                description=row.get("description") or None,
                url=row.get("url") or None,
                updated_at=updated,
                labels=tabular.split_pipe(row.get("labels")),
                estimate=estimate,
                estimate_unit=row.get("estimateunit", "unit") or None,
                custom_fields={k: v for k, v in row.cells.items() if k not in KNOWN and v},
                team_hint=row.get("team") or None,
            )
