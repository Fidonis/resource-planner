"""The demo data: loaded once into an empty installation, and only when the server allows it."""

from __future__ import annotations

from datetime import date

import pytest
from tests.api.support import Api
from tests.api.world import ABSENCES, ASSIGNMENTS, MEMBERS, PLANNING, SKILLS, TASKS, TEAMS, team

from planner_api import clock
from planner_api.config import Settings

DEMO = "/api/v1/demo-data"
TODAY = date(2030, 1, 9)  # a Wednesday; the demo weeks start on the Monday before


async def total(admin, url: str) -> int:  # type: ignore[no-untyped-def]
    return int((await admin.get(url, params={"pageSize": 1})).json()["total"])


async def test_the_demo_data_fills_an_empty_installation(api: Api) -> None:
    admin = await api.admin()
    with clock.pinned(TODAY):
        response = await admin.post(DEMO)
    assert response.status_code == 201, response.text
    counts = response.json()
    assert (counts["teams"], counts["skills"], counts["members"], counts["tasks"]) == (3, 8, 12, 13)
    assert counts["assignments"] > 0 and counts["absences"] > 0
    assert await total(admin, TEAMS) == 3 and await total(admin, SKILLS) == 8
    assert await total(admin, MEMBERS) == 12 and await total(admin, TASKS) == 13
    assert await total(admin, ASSIGNMENTS) == counts["assignments"]
    assert await total(admin, ABSENCES) == counts["absences"]


async def test_the_demo_data_shows_what_the_board_is_for(api: Api) -> None:
    admin = await api.admin()
    with clock.pinned(TODAY):
        assert (await admin.post(DEMO)).status_code == 201
        board = (await admin.get(PLANNING, params={"weeks": 4})).json()
        tasks = (await admin.get(TASKS, params={"pageSize": 50})).json()["items"]
    assert board["start"] == "2030-01-07"
    people = {m["displayName"]: m for t in board["teams"] for m in t["members"]}
    assert any(w["overbooked"] for w in people["Ben Costa"]["weeks"]), "Ben is overbooked"
    assert any(w["absentDays"] > 0 for m in people.values() for w in m["weeks"])
    assert any(len(t["efforts"]) == 5 for t in tasks), "one task needs five skills"
    assert {t["status"] for t in tasks} >= {"open", "done"}


async def test_the_demo_data_is_refused_once_the_installation_holds_data(api: Api) -> None:
    admin = await api.admin()
    first = await admin.post(DEMO)
    assert first.status_code == 201
    second = await admin.post(DEMO)
    assert second.status_code == 409 and second.json()["code"] == "DEMO_SEED_NOT_EMPTY"
    assert await total(admin, TEAMS) == 3, "a refused load changes nothing"


async def test_a_single_team_is_enough_to_refuse_the_demo_data(api: Api) -> None:
    admin = await api.admin()
    await team(admin)
    refused = await admin.post(DEMO)
    assert refused.status_code == 409 and refused.json()["code"] == "DEMO_SEED_NOT_EMPTY"
    assert await total(admin, TEAMS) == 1


async def test_the_demo_data_stays_off_unless_the_server_allows_it(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = await api.admin()
    monkeypatch.setattr(api.settings, "enable_demo_seed", False)
    refused = await admin.post(DEMO)
    assert refused.status_code == 403 and refused.json()["code"] == "DEMO_SEED_DISABLED"
    assert await total(admin, TEAMS) == 0


async def test_only_an_administrator_can_load_the_demo_data(api: Api) -> None:
    for role in ("planner", "master-data-admin", "viewer"):
        caller = await api.as_role(role)
        assert (await caller.post(DEMO)).status_code == 403, role
    assert (await api.anonymous().post(DEMO)).status_code == 401
    assert await total(await api.admin(), TEAMS) == 0


def test_the_seed_command_refuses_to_run_without_the_switch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from planner_api import cli

    off = Settings(database_url="postgresql+psycopg://x:x@localhost/x", enable_demo_seed=False)
    monkeypatch.setattr(cli, "get_settings", lambda: off)
    assert cli.main(["seed-demo", "--today", "2030-01-09"]) == 1
    assert "ENABLE_DEMO_SEED" in capsys.readouterr().err
