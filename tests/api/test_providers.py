"""The built-in providers against the contract kit, and the scheduler."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from tests.api.provider_kit import check_provider
from tests.api.support import Api
from tests.api.sync_support import FakeTaskSource, integration, write
from tests.api.test_sync_people import USERS, keycloak_handler
from tests.api.world import skill

from planner_api.config import Settings
from planner_api.providers.csv_absences import CsvAbsenceSource
from planner_api.providers.csv_members import CsvDirectory
from planner_api.providers.csv_tasks import CsvTaskSource
from planner_api.providers.keycloak import KeycloakDirectory
from planner_api.providers.registry import GROUP, ProviderRegistry, load_entry_points
from planner_api.sync.scheduler import SyncScheduler, validate_schedule


async def test_the_csv_task_provider_keeps_the_contract(tmp_path: Path) -> None:
    write(tmp_path, "t.csv", "Key;Title\nA-1;One\nA-2;Two\n")
    await check_provider(
        CsvTaskSource, Settings(import_dir=tmp_path), {"path": "t.csv"}, expect_records=2
    )


async def test_the_csv_absence_provider_keeps_the_contract(tmp_path: Path) -> None:
    write(tmp_path, "a.csv", "Person;From;To;Type\nada@example.test;2030-01-08;2030-01-09;V\n")
    await check_provider(
        CsvAbsenceSource, Settings(import_dir=tmp_path), {"path": "a.csv"}, expect_records=1
    )


async def test_the_csv_directory_keeps_the_contract(tmp_path: Path) -> None:
    write(tmp_path, "p.csv", "Name;Email\nAda;ada@example.test\nBob;bob@example.test\n")
    await check_provider(
        CsvDirectory, Settings(import_dir=tmp_path), {"path": "p.csv"}, expect_records=2
    )


async def test_the_keycloak_provider_keeps_the_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        KeycloakDirectory, "transport", httpx.MockTransport(keycloak_handler()), raising=False
    )
    config = {
        "baseUrl": "http://keycloak.test",
        "realm": "planner",
        "clientId": "c",
        "clientSecret": "right",
    }
    found = await check_provider(KeycloakDirectory, Settings(), config, expect_records=3)
    assert len(found) == len(USERS) - 1  # the service account is left out


async def test_a_provider_outside_the_package_keeps_the_contract() -> None:
    await check_provider(FakeTaskSource, Settings(), {"count": 3}, expect_records=3)


def test_a_broken_plug_in_is_left_out_and_the_others_load() -> None:
    from importlib.metadata import EntryPoint

    broken: list[str] = []
    registry = load_entry_points(
        [
            EntryPoint("missing", "tests.api.no_such_module:Nothing", GROUP),
            EntryPoint("fake", "tests.api.sync_support:FakeTaskSource", GROUP),
        ],
        on_error=lambda point, _exc: broken.append(point.name),
    )
    assert broken == ["missing"]
    assert registry.get("task_source", "fake") is FakeTaskSource


def test_a_class_without_a_kind_cannot_be_registered() -> None:
    class Odd:
        key = "odd"
        kind = "unknown"

    with pytest.raises(ValueError):
        ProviderRegistry([Odd])


def test_a_schedule_is_a_cron_line_of_five_fields() -> None:
    validate_schedule("0 6 * * 1-5")
    for bad in ("soon", "* * *", "61 * * * *"):
        with pytest.raises(ValueError):
            validate_schedule(bad)


async def test_the_scheduler_follows_the_enabled_integrations(
    sync_api: Api, imports_dir: Path
) -> None:
    admin = await sync_api.admin()
    await skill(admin, "Backend")
    path = write(imports_dir, "tasks.csv", "Key;Title\nS-1;Scheduled\n")
    made = await integration(admin, "task_source", path, schedule="0 6 * * *")
    plain = await integration(admin, "task_source", path, name="Manual only")
    scheduler = SyncScheduler(sync_api.app.state.sync_engine, sync_api.factory)
    await scheduler.start()
    try:
        assert scheduler.job_ids() == [f"integration:{made['id']}"]
        off = await admin.put(
            f"/api/v1/integrations/{made['id']}",
            {
                "version": made["version"],
                "name": made["name"],
                "enabled": False,
                "schedule": made["schedule"],
                "config": {"path": path},
            },
        )
        assert off.status_code == 200, off.text
        await scheduler.refresh()
        assert scheduler.job_ids() == []

        # what the cron job does when it fires
        await scheduler._run(__import__("uuid").UUID(plain["id"]))
        runs = (await admin.get(f"/api/v1/integrations/{plain['id']}/runs")).json()["items"]
        assert [r["triggeredBy"] for r in runs] == ["schedule"]
    finally:
        await scheduler.stop()
