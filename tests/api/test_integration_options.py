"""Choices loaded from the source, the reset of incremental reading and the full-run rule."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import update
from tests.api.support import Api, Caller
from tests.api.sync_support import INTEGRATIONS, FakeTaskSource, sync

from planner_api.models import SyncRun
from planner_api.services.context import utcnow
from planner_api.sync.scheduler import SyncScheduler

OPTIONS = f"{INTEGRATIONS}/field-options"


def ask(config: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "type": "task_source",
        "providerKey": "fake",
        "config": config,
        "field": "choices",
        **extra,
    }


async def fake(admin: Caller, **config: Any) -> dict[str, Any]:
    return await admin.create(
        INTEGRATIONS,
        {
            "name": "Fake",
            "type": "task_source",
            "providerKey": "fake",
            "config": config,
            "mapping": {},
        },
    )


async def test_choices_are_loaded_for_a_draft_that_could_not_be_saved(sync_api: Api) -> None:
    admin = await sync_api.admin()
    loaded = await admin.post(OPTIONS, ask({"scope": "SOME", "count": 3}))
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["items"] == [
        {"value": "C0", "label": "Choice 0"},
        {"value": "C1", "label": "Choice 1"},
        {"value": "C2", "label": "Choice 2"},
    ]
    # saving the same settings is strict: SOME needs a choice
    refused = await admin.post(
        INTEGRATIONS,
        {
            "name": "Fake",
            "type": "task_source",
            "providerKey": "fake",
            "config": {"scope": "SOME"},
            "mapping": {},
        },
    )
    assert refused.status_code == 422
    assert refused.json()["errors"][0]["field"].startswith("config")


async def test_a_stored_secret_is_used_only_for_the_stored_connection(sync_api: Api) -> None:
    admin = await sync_api.admin()
    made = await fake(admin, token="right")
    same = await admin.post(OPTIONS, ask({"count": 1}, integrationId=made["id"]))
    assert same.status_code == 200, same.text
    assert same.json()["items"][0]["label"] == "Choice 0 (signed in)"

    moved = await admin.post(
        OPTIONS, ask({"count": 1, "address": "fake://elsewhere"}, integrationId=made["id"])
    )
    assert moved.status_code == 422, moved.text
    error = moved.json()["errors"][0]
    assert (error["field"], error["code"]) == ("config.token", "secret_needed")

    again = await admin.post(
        OPTIONS,
        ask({"count": 1, "address": "fake://elsewhere", "token": "new"}, integrationId=made["id"]),
    )
    assert again.status_code == 200 and "(signed in)" in again.json()["items"][0]["label"]
    # without the integration nothing stored is used
    plain = await admin.post(OPTIONS, ask({"count": 1}))
    assert plain.json()["items"][0]["label"] == "Choice 0"


async def test_errors_of_the_source_and_settings_without_choices(sync_api: Api) -> None:
    admin = await sync_api.admin()
    down = await admin.post(OPTIONS, ask({"address": "fake://down"}))
    assert down.status_code == 422 and down.json()["code"] == "PROVIDER_ERROR"
    assert down.json()["detail"] == "The fake tracker is down."
    none = await admin.post(OPTIONS, ask({}, field="count"))
    assert none.status_code == 422 and none.json()["errors"][0]["code"] == "no_options"
    csv = await admin.post(
        OPTIONS, {"type": "task_source", "providerKey": "csv", "config": {}, "field": "path"}
    )
    assert csv.status_code == 422
    unknown = await admin.post(OPTIONS, ask({}, providerKey="nothing"))
    assert unknown.status_code == 422 and unknown.json()["code"] == "PROVIDER_UNKNOWN"
    planner = await sync_api.as_role("planner")
    assert (await planner.post(OPTIONS, ask({}))).status_code == 403


async def test_changed_settings_make_the_next_run_read_everything(sync_api: Api) -> None:
    admin = await sync_api.admin()
    made = await fake(admin, token="right")
    await sync(admin, made["id"])
    stored = (await admin.get(f"{INTEGRATIONS}/{made['id']}")).json()
    assert stored["lastSyncAt"]
    body = {k: stored[k] for k in ("name", "enabled", "schedule", "config", "mapping")}

    kept = await admin.put(f"{INTEGRATIONS}/{made['id']}", {**body, "version": stored["version"]})
    assert kept.status_code == 200, kept.text
    assert kept.json()["lastSyncAt"] == stored["lastSyncAt"]

    changed = await admin.put(
        f"{INTEGRATIONS}/{made['id']}",
        {**body, "config": {**body["config"], "count": 3}, "version": kept.json()["version"]},
    )
    assert changed.status_code == 200 and changed.json()["lastSyncAt"] is None

    await sync(admin, made["id"])
    stored = (await admin.get(f"{INTEGRATIONS}/{made['id']}")).json()
    assert stored["lastSyncAt"]
    renewed = await admin.put(
        f"{INTEGRATIONS}/{made['id']}",
        {**body, "config": {**stored["config"], "token": "new"}, "version": stored["version"]},
    )
    assert renewed.status_code == 200 and renewed.json()["lastSyncAt"] is None


async def test_scheduled_runs_read_everything_once_per_interval(
    sync_api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(FakeTaskSource, "supports_since", True)
    admin = await sync_api.admin()
    made = await fake(admin)
    engine = sync_api.app.state.sync_engine
    ident = uuid.UUID(made["id"])
    scheduler = SyncScheduler(engine, sync_api.factory)

    assert await engine.full_due(ident) is True
    await scheduler._run(ident)
    assert await engine.full_due(ident) is False
    await scheduler._run(ident)
    runs = (await admin.get(f"{INTEGRATIONS}/{made['id']}/runs")).json()["items"]
    assert [r["full"] for r in runs] == [False, True]  # newest first

    async with sync_api.factory() as session:
        await session.execute(
            update(SyncRun)
            .where(SyncRun.integration_id == ident)
            .values(started_at=utcnow() - timedelta(hours=25))
        )
        await session.commit()
    assert await engine.full_due(ident) is True
    await scheduler._run(ident)
    runs = (await admin.get(f"{INTEGRATIONS}/{made['id']}/runs")).json()["items"]
    assert runs[0]["full"] is True and runs[0]["triggeredBy"] == "schedule"


async def test_a_first_run_of_an_incremental_source_is_complete(
    sync_api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(FakeTaskSource, "supports_since", True)
    admin = await sync_api.admin()
    made = await fake(admin)
    first = await sync(admin, made["id"])
    assert first["full"] is True
    second = await sync(admin, made["id"])
    assert second["full"] is False


async def test_providers_that_always_read_everything_are_never_due(sync_api: Api) -> None:
    admin = await sync_api.admin()
    made = await fake(admin)
    engine = sync_api.app.state.sync_engine
    assert await engine.full_due(uuid.UUID(made["id"])) is False
    assert await engine.full_due(uuid.uuid4()) is False
