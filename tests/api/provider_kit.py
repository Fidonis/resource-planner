"""A contract test kit for providers (FR-INT-09).

Call :func:`check_provider` from the test of a provider package with a valid configuration. It
checks what the sync engine relies on, not what a provider reads.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from planner_api.config import Settings
from planner_api.providers.base import (
    KIND_ABSENCE_SOURCE,
    KIND_DIRECTORY,
    KIND_TASK_SOURCE,
    KINDS,
    ExternalAbsence,
    ExternalPerson,
    ExternalTask,
    SourceProblem,
    TestResult,
)

RECORD = {
    KIND_TASK_SOURCE: ExternalTask,
    KIND_DIRECTORY: ExternalPerson,
    KIND_ABSENCE_SOURCE: ExternalAbsence,
}


async def check_provider(
    provider_class: type[Any], settings: Settings, config: dict[str, Any], *, expect_records: int
) -> list[Any]:
    """Raises ``AssertionError`` when the provider breaks the contract; returns what it read."""
    assert provider_class.kind in KINDS, "kind must be one of the three provider kinds"
    assert isinstance(provider_class.key, str) and provider_class.key
    assert isinstance(provider_class.title, str) and provider_class.title
    assert issubclass(provider_class.config_model, BaseModel)

    provider = provider_class(settings)
    schema = provider.describe_config_schema()
    json.dumps(schema)  # the admin form is built from it, so it must be plain JSON
    assert schema.get("type") == "object"

    checked = provider_class.config_model.model_validate(config)
    tested = await provider.test_connection(checked)
    assert isinstance(tested, TestResult) and tested.ok, tested.message
    assert tested.message

    if provider.kind == KIND_TASK_SOURCE:
        stream = provider.fetch_tasks(checked, None)
    elif provider.kind == KIND_DIRECTORY:
        stream = provider.fetch_members(checked)
    else:
        from datetime import date

        stream = provider.fetch_absences(checked, date(2000, 1, 1), date(2100, 1, 1))
    found = [item async for item in stream]
    records = [item for item in found if not isinstance(item, SourceProblem)]
    assert all(isinstance(item, RECORD[provider.kind]) for item in records)
    assert len(records) == expect_records
    ids = [item.external_id for item in records]
    assert len(set(ids)) == len(ids), "external ids must be unique within a read"
    # reading twice gives the same ids: the engine matches records by them
    again = []
    if provider.kind == KIND_TASK_SOURCE:
        again = [i async for i in provider.fetch_tasks(checked, None)]
    elif provider.kind == KIND_DIRECTORY:
        again = [i async for i in provider.fetch_members(checked)]
    assert not again or [i.external_id for i in again if hasattr(i, "external_id")] == ids
    return found
