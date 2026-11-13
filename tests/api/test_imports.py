"""CSV import and export of tasks, absences and assignments (FR-TASK-05, FR-ABS-06, FR-OVR-04)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from tests.api.support import Api, Caller
from tests.api.world import (
    ABSENCES,
    ASSIGNMENTS,
    TASKS,
    audit_count,
    booked,
    day,
    member,
    skill,
    task,
    team,
    week,
)

TASK_HEADER = "Key;Title;Team;Efforts;Status"
ABSENCE_HEADER = "Name;From;To;Type;Note;StartPart;EndPart"


async def upload(caller: Caller, url: str, text: str | bytes, **params: Any) -> httpx.Response:
    body = text if isinstance(text, bytes) else text.encode("utf-8")
    return await caller.post(
        url + "/import-csv",
        content=body,
        headers={"Content-Type": "text/csv"},
        params=params or None,
    )


async def import_tasks(caller: Caller, text: str | bytes, **params: Any) -> dict[str, Any]:
    response = await upload(caller, TASKS, text, **params)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


async def import_absences(caller: Caller, text: str, **params: Any) -> dict[str, Any]:
    response = await upload(caller, ABSENCES, text, **params)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


async def tasks_by_key(caller: Caller) -> dict[str, dict[str, Any]]:
    items = (await caller.get(TASKS, params={"pageSize": 200})).json()["items"]
    return {t["key"]: t for t in items}


def efforts(row: dict[str, Any]) -> dict[str, float]:
    return {e["skillKey"]: e["personDays"] for e in row["efforts"]}


# --- tasks -------------------------------------------------------------------------------


async def test_tasks_are_created_updated_and_left_alone_by_an_import(api: Api) -> None:
    admin = await api.admin()
    await skill(admin, "Backend"), await skill(admin, "QA")
    await team(admin, "Alpha")
    first = f"{TASK_HEADER}\r\nT-1;First;Alpha;Backend=2|QA=1,5;\r\nT-2;Second;;Backend=3;done\r\n"
    result = await import_tasks(admin, first)
    assert (result["created"], result["updated"], result["skipped"], result["errors"]) == (
        2,
        0,
        0,
        [],
    )
    found = await tasks_by_key(admin)
    assert efforts(found["T-1"]) == {"Backend": 2, "QA": 1.5}
    assert found["T-1"]["ownerTeamName"] == "Alpha" and found["T-1"]["status"] == "open"
    assert found["T-2"]["status"] == "done" and found["T-2"]["ownerTeamName"] is None

    again = await import_tasks(admin, first)
    assert (again["created"], again["updated"], again["skipped"]) == (0, 0, 2)

    changed = await import_tasks(admin, f"{TASK_HEADER}\r\nT-1;First, renamed;;Backend=2|QA=2;\r\n")
    assert (changed["updated"], changed["skipped"]) == (1, 0)
    row = (await tasks_by_key(admin))["T-1"]
    assert row["title"] == "First, renamed" and row["ownerTeamName"] == "Alpha", "empty cells keep"
    assert efforts(row) == {"Backend": 2, "QA": 2} and row["version"] > 1


async def test_a_task_import_accepts_commas_tabs_a_bom_and_decimal_commas(api: Api) -> None:
    admin = await api.admin()
    await skill(admin, "Backend")
    comma = await import_tasks(admin, "﻿Key,Title,Team,Efforts\nA-1,Comma,,Backend=1.25\n")
    tab = await import_tasks(admin, "Key\tTitle\tTeam\tEfforts\nA-2\tTab\t\tBackend=0,5\n")
    assert comma["created"] == 1 and tab["created"] == 1
    found = await tasks_by_key(admin)
    assert efforts(found["A-1"]) == {"Backend": 1.25} and efforts(found["A-2"]) == {"Backend": 0.5}


async def test_a_file_without_a_header_is_read_from_the_first_line(api: Api) -> None:
    admin = await api.admin()
    result = await import_tasks(admin, "B-1;No header;;\n")
    assert result["created"] == 1 and "B-1" in await tasks_by_key(admin)


async def test_rows_with_errors_are_reported_by_line_and_the_rest_is_applied(api: Api) -> None:
    admin = await api.admin()
    await skill(admin, "Backend")
    text = (
        f"{TASK_HEADER}\n"
        "C-1;Fine;;Backend=1;\n"
        ";No key;;;\n"
        "C-3;;;;\n"
        "C-4;Bad status;;;started\n"
        "C-5;Twice;;Backend=1|backend=2;\n"
        "C-6;Zero;;Backend=0;\n"
        "C-7;Bad number;;Backend=abc;\n"
        "C-8;No separator;;Backend;\n"
        "C-9;Team;Nowhere;;\n"
        "C-10;Skill;;Nothing=1;\n"
    )
    result = await import_tasks(admin, text)
    assert (result["created"], result["updated"], result["skipped"]) == (1, 0, 0)
    by_line = {e["line"]: e for e in result["errors"]}
    assert sorted(by_line) == [3, 4, 5, 6, 7, 8, 9, 10, 11]
    assert by_line[3]["field"] == "Key" and by_line[4]["field"] == "Title"
    assert by_line[5]["field"] == "Status" and by_line[6]["code"] == "duplicate"
    assert by_line[10]["code"] == "unknown_team" and by_line[11]["code"] == "unknown_skill"
    assert result["unknownSkills"] == ["Nothing"]
    assert set(await tasks_by_key(admin)) == {"C-1"}


async def test_unknown_skills_are_created_on_request(api: Api) -> None:
    admin = await api.admin()
    text = f"{TASK_HEADER}\nD-1;New skills;;Rust=2|Go=1;\n"
    refused = await import_tasks(admin, text)
    assert refused["created"] == 0 and sorted(refused["unknownSkills"]) == ["Go", "Rust"]
    accepted = await import_tasks(admin, text, createSkills="true")
    assert accepted["created"] == 1 and sorted(accepted["createdSkills"]) == ["Go", "Rust"]
    assert accepted["unknownSkills"] == [] and accepted["errors"] == []
    assert efforts((await tasks_by_key(admin))["D-1"]) == {"Go": 1, "Rust": 2}
    skills = (await admin.get("/api/v1/skills")).json()["items"]
    assert {"Go", "Rust"} <= {s["key"] for s in skills}


async def test_dm03_an_import_may_not_cut_the_effort_below_what_is_assigned(api: Api) -> None:
    admin = await api.admin()
    backend = await skill(admin, "Backend")
    ada = await member(admin, (await team(admin))["id"], "Ada", (backend["id"],))
    work = await task(admin, "E-1", {backend["id"]: "5"})
    await booked(admin, work["id"], backend["id"], ada["id"], week(0), 2)
    await booked(admin, work["id"], backend["id"], ada["id"], week(1), 2)
    text = f"{TASK_HEADER}\nE-1;Task E-1;;Backend=1;\n"

    refused = await import_tasks(admin, text)
    assert (refused["updated"], len(refused["errors"])) == (0, 1)
    assert (
        refused["errors"][0]["line"] == 2
        and refused["errors"][0]["code"] == "effort_below_assigned"
    )
    assert efforts((await tasks_by_key(admin))["E-1"]) == {"Backend": 5}

    forced = await import_tasks(admin, text, force="true")
    assert (forced["updated"], forced["errors"]) == (1, [])
    rows = (await admin.get(ASSIGNMENTS, params={"sort": "weekStart"})).json()["items"]
    assert [(r["weekStart"], r["personDays"]) for r in rows] == [(week(0), 1)]


async def test_a_task_import_with_dry_run_saves_nothing(api: Api) -> None:
    admin = await api.admin()
    await skill(admin, "Backend")
    before = await audit_count(admin)
    result = await import_tasks(admin, f"{TASK_HEADER}\nF-1;Probe;;Backend=1;\n", dryRun="true")
    assert result["dryRun"] is True and result["created"] == 1
    assert await tasks_by_key(admin) == {}
    assert await audit_count(admin) == before
    real = await import_tasks(admin, f"{TASK_HEADER}\nF-1;Probe;;Backend=1;\n")
    assert real["dryRun"] is False and set(await tasks_by_key(admin)) == {"F-1"}
    assert await audit_count(admin) == before + 1


async def test_a_file_that_is_not_utf8_or_too_large_is_refused(api: Api) -> None:
    admin = await api.admin()
    latin1 = await upload(admin, TASKS, "Key;Title\nG-1;Größe\n".encode("latin-1"))
    assert latin1.status_code == 422 and latin1.json()["code"] == "IMPORT_FAILED"
    huge = await upload(admin, TASKS, b"Key;Title\n" + b"x" * (5 * 1024 * 1024 + 1))
    assert huge.status_code == 413 and huge.json()["code"] == "IMPORT_FAILED"
    assert await tasks_by_key(admin) == {}


async def test_only_users_who_may_write_tasks_can_import_them(api: Api) -> None:
    viewer = await api.as_role("viewer")
    refused = await upload(viewer, TASKS, f"{TASK_HEADER}\nH-1;Nope;;;\n")
    assert refused.status_code == 403 and refused.json()["permission"] == "task:write"
    planner = await api.as_role("planner")
    assert (await import_tasks(planner, f"{TASK_HEADER}\nH-1;Fine;;;\n"))["created"] == 1


async def test_the_task_export_can_be_imported_again_without_changes(api: Api) -> None:
    admin = await api.admin()
    backend, qa = await skill(admin, "Backend"), await skill(admin, "QA")
    alpha = await team(admin, "Alpha")
    await task(admin, "X-1", {backend["id"]: "2.5", qa["id"]: "1"}, ownerTeamId=alpha["id"])
    await task(admin, "X-2", {backend["id"]: "1"}, title="Plain, with; punctuation")

    exported = await admin.get(TASKS + "/export-csv")
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")
    assert 'filename="tasks.csv"' in exported.headers["content-disposition"]
    lines = exported.text.splitlines()
    assert lines[0] == TASK_HEADER and len(lines) == 3
    assert "Backend=2.5|QA=1" in lines[1] and "Alpha" in lines[1]

    again = await import_tasks(admin, exported.content)
    assert (again["created"], again["updated"], again["skipped"], again["errors"]) == (0, 0, 2, [])


async def test_an_export_does_not_let_a_spreadsheet_run_a_formula(api: Api) -> None:
    admin = await api.admin()
    await task(admin, "Y-1", {}, title='=HYPERLINK("http://example.test","x")')
    await task(admin, "Y-2", {}, title="+1+1")
    text = (await admin.get(TASKS + "/export-csv")).text
    assert ";\"'=HYPERLINK" in text and ";'+1+1" in text
    assert ";=HYPERLINK" not in text and ';"=HYPERLINK' not in text


# --- absences ----------------------------------------------------------------------------


async def test_absences_are_imported_once_and_skipped_when_they_exist(api: Api) -> None:
    admin = await api.admin()
    alpha = await team(admin, "Alpha")
    await member(admin, alpha["id"], "Ada")
    text = (
        f"{ABSENCE_HEADER}\n"
        "Ada;07.01.2030;11.01.2030;vacation;Alps;;\n"
        "*;2030-01-10;2030-01-10;public-holiday;;;\n"
        "ada;2030-01-14;2030-01-14;Sick;;AM;\n"
    )
    result = await import_absences(admin, text)
    assert (result["created"], result["skipped"], result["errors"]) == (3, 0, [])
    rows = (await admin.get(ABSENCES, params={"sort": "startDate"})).json()["items"]
    assert [(r["memberName"], r["startDate"], r["endDate"], r["typeKey"]) for r in rows] == [
        ("Ada", day(0), day(4), "vacation"),
        (None, day(3), day(3), "public-holiday"),
        ("Ada", day(7), day(7), "sick"),
    ]
    assert rows[0]["note"] == "Alps"
    assert (rows[2]["startPart"], rows[2]["endPart"], rows[2]["workingDays"]) == ("AM", "AM", 0.5)

    again = await import_absences(admin, text)
    assert (again["created"], again["skipped"]) == (0, 3)


async def test_absence_rows_with_errors_are_reported_and_the_others_kept(api: Api) -> None:
    admin = await api.admin()
    alpha = await team(admin, "Alpha")
    await member(admin, alpha["id"], "Ada")
    await member(admin, alpha["id"], "Twin")
    await member(admin, (await team(admin, "Beta"))["id"], "Twin")
    text = (
        f"{ABSENCE_HEADER}\n"
        "Ada;2030-01-07;2030-01-07;vacation;;;\n"
        ";2030-01-07;2030-01-07;vacation;;;\n"
        "Nobody;2030-01-07;2030-01-07;vacation;;;\n"
        "Twin;2030-01-07;2030-01-07;vacation;;;\n"
        "Ada;31.02.2030;2030-01-07;vacation;;;\n"
        "Ada;2030-01-07;;vacation;;;\n"
        "Ada;2030-01-09;2030-01-07;vacation;;;\n"
        "Ada;2030-01-07;2030-01-07;nonsense;;;\n"
        "Ada;2030-01-07;2030-01-07;vacation;;NOON;\n"
    )
    result = await import_absences(admin, text)
    assert (result["created"], result["skipped"]) == (1, 0)
    by_line = {e["line"]: e for e in result["errors"]}
    assert sorted(by_line) == [3, 4, 5, 6, 7, 8, 9, 10]
    assert by_line[4]["code"] == "unknown_member" and by_line[5]["code"] == "ambiguous_member"
    assert by_line[6]["field"] == "From" and by_line[7]["field"] == "To"
    assert by_line[9]["code"] == "unknown_type" and by_line[10]["field"] == "StartPart"
    assert (await admin.get(ABSENCES)).json()["total"] == 1


async def test_an_absence_import_with_dry_run_saves_nothing(api: Api) -> None:
    admin = await api.admin()
    await member(admin, (await team(admin))["id"], "Ada")
    before = await audit_count(admin)
    result = await import_absences(
        admin, f"{ABSENCE_HEADER}\nAda;2030-01-07;2030-01-08;vacation;;;\n", dryRun="true"
    )
    assert result["dryRun"] is True and result["created"] == 1
    assert (await admin.get(ABSENCES)).json()["total"] == 0
    assert await audit_count(admin) == before


async def test_a_team_scoped_import_skips_the_rows_it_may_not_write(api: Api) -> None:
    admin = await api.admin()
    alpha, beta = await team(admin, "Alpha"), await team(admin, "Beta")
    await member(admin, alpha["id"], "Ada")
    await member(admin, beta["id"], "Bob")
    lead = await api.as_role("team-lead", teams=[uuid.UUID(alpha["id"])])
    text = (
        f"{ABSENCE_HEADER}\n"
        "Ada;2030-01-07;2030-01-07;vacation;;;\n"
        "Bob;2030-01-07;2030-01-07;vacation;;;\n"
        "*;2030-01-08;2030-01-08;public-holiday;;;\n"
    )
    result = await import_absences(lead, text)
    assert (result["created"], [e["line"] for e in result["errors"]]) == (1, [3, 4])
    assert {e["code"] for e in result["errors"]} == {"permission_denied"}
    assert (await admin.get(ABSENCES)).json()["total"] == 1

    viewer = await api.as_role("viewer")
    assert (await upload(viewer, ABSENCES, text)).status_code == 403


async def test_the_absence_export_can_be_imported_again_without_changes(api: Api) -> None:
    admin = await api.admin()
    await member(admin, (await team(admin))["id"], "Ada")
    await import_absences(
        admin,
        f"{ABSENCE_HEADER}\n"
        "Ada;2030-01-07;2030-01-11;vacation;Alps;;\n"
        "*;2030-01-10;2030-01-10;public-holiday;;;\n"
        "Ada;2030-01-14;2030-01-14;sick;;PM;PM\n",
    )
    exported = await admin.get(ABSENCES + "/export-csv")
    assert exported.status_code == 200 and exported.text.splitlines()[0] == ABSENCE_HEADER
    assert len(exported.text.splitlines()) == 4
    window = await admin.get(ABSENCES + "/export-csv", params={"from": day(7), "to": day(8)})
    assert len(window.text.splitlines()) == 2
    again = await import_absences(admin, exported.text)
    assert (again["created"], again["skipped"], again["errors"]) == (0, 3, [])


# --- assignments -------------------------------------------------------------------------


async def test_the_assignment_export_lists_the_horizon_in_week_and_member_order(api: Api) -> None:
    admin = await api.admin()
    backend = await skill(admin)
    alpha = await team(admin, "Alpha")
    ada = await member(admin, alpha["id"], "Ada", (backend["id"],))
    bob = await member(admin, alpha["id"], "Bob", (backend["id"],))
    work = await task(admin, "T-1", {backend["id"]: "6"})
    await booked(admin, work["id"], backend["id"], bob["id"], week(0), 1)
    await booked(admin, work["id"], backend["id"], ada["id"], week(1), 2.5)
    await booked(admin, work["id"], backend["id"], ada["id"], week(0), 1)
    await booked(admin, work["id"], backend["id"], ada["id"], week(9), 1)

    response = await admin.get(ASSIGNMENTS + "/export-csv", params={"from": week(), "weeks": 2})
    assert response.status_code == 200
    assert response.text.splitlines() == [
        "Task;Skill;Member;Team;Week;PersonDays",
        f"T-1;Backend;Ada;Alpha;{week(0)};1",
        f"T-1;Backend;Bob;Alpha;{week(0)};1",
        f"T-1;Backend;Ada;Alpha;{week(1)};2.5",
    ]
    viewer = await api.as_role("viewer")
    assert (await viewer.get(ASSIGNMENTS + "/export-csv")).status_code == 200
    assert (await api.anonymous().get(ASSIGNMENTS + "/export-csv")).status_code == 401
