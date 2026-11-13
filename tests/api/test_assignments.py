"""Assignments: rules, merging, dry run, concurrency, scope (acceptance 5 to 8, 12, 13)."""

from __future__ import annotations

import uuid
from typing import Any

from tests.api.support import Api, Caller
from tests.api.world import (
    ASSIGNMENTS,
    AUDIT,
    MEMBERS,
    TASKS,
    assign,
    audit_count,
    booked,
    member,
    skill,
    task,
    team,
    week,
)


class World:
    """One team with Ada (Backend) and Bob (Backend, QA), and one task needing both skills."""

    admin: Caller
    backend: dict[str, Any]
    qa: dict[str, Any]
    team: dict[str, Any]
    ada: dict[str, Any]
    bob: dict[str, Any]
    task: dict[str, Any]


async def world(api: Api, *, backend_days: str = "6") -> World:
    w = World()
    w.admin = await api.admin()
    w.backend, w.qa = await skill(w.admin, "Backend"), await skill(w.admin, "QA")
    w.team = await team(w.admin, "Alpha")
    w.ada = await member(w.admin, w.team["id"], "Ada", (w.backend["id"],), allocationPercent=80)
    w.bob = await member(
        w.admin, w.team["id"], "Bob", (w.backend["id"], w.qa["id"]), allocationPercent=100
    )
    w.task = await task(w.admin, "T-1", {w.backend["id"]: backend_days, w.qa["id"]: "2"})
    return w


async def test_creating_an_assignment_reports_its_effect(api: Api) -> None:
    w = await world(api)
    response = await assign(w.admin, w.task["id"], w.backend["id"], w.ada["id"], week(), 2)
    assert response.status_code == 201, response.text
    body = response.json()
    assert response.headers["etag"] == '"1"'
    assert response.headers["location"] == f"/api/v1/assignments/{body['id']}"
    assert (body["taskKey"], body["skillKey"], body["memberName"]) == ("T-1", "Backend", "Ada")
    assert body["personDays"] == 2
    assert body["dryRun"] is False
    assert body["overbooked"] is False
    assert body["overplanned"] is False
    assert body["mergedInto"] is None
    assert body["memberWeeks"] == [
        {
            "memberId": w.ada["id"],
            "weekStart": week(),
            "presentDays": 5,
            "absentDays": 0,
            "capacity": 4,
            "booked": 2,
            "free": 2,
            "overbooked": False,
            "full": False,
        }
    ]
    assert body["taskSkill"] == {
        "taskId": w.task["id"],
        "skillId": w.backend["id"],
        "effort": 6,
        "assigned": 2,
        "open": 4,
        "overplanned": False,
    }
    assert body["writable"] is True and body["closed"] is False


async def test_acceptance_5_a_member_without_the_skill_is_refused(api: Api) -> None:
    w = await world(api)
    response = await assign(w.admin, w.task["id"], w.qa["id"], w.ada["id"])
    assert response.status_code == 422
    problem = response.json()
    assert problem["code"] == "ASSIGNMENT_SKILL_MISMATCH"
    assert problem["errors"][0]["field"] == "skillId"
    assert (await w.admin.get(ASSIGNMENTS)).json()["total"] == 0


async def test_acceptance_6_overbooking_is_allowed_and_flagged(api: Api) -> None:
    w = await world(api)
    first = await assign(w.admin, w.task["id"], w.backend["id"], w.ada["id"], week(), 4)
    assert first.json()["overbooked"] is False
    assert first.json()["memberWeeks"][0]["full"] is True
    second = await assign(w.admin, w.task["id"], w.backend["id"], w.ada["id"], week(), 1)
    assert second.status_code == 200, "merged into the existing assignment"
    body = second.json()
    assert body["overbooked"] is True
    assert body["memberWeeks"][0]["free"] == -1


async def test_over_planning_is_flagged_and_never_clipped(api: Api) -> None:
    w = await world(api, backend_days="2")
    await booked(w.admin, w.task["id"], w.backend["id"], w.ada["id"], week(0), 2)
    over = await assign(w.admin, w.task["id"], w.backend["id"], w.ada["id"], week(1), 1)
    assert over.status_code == 201
    assert over.json()["overplanned"] is True
    assert over.json()["taskSkill"]["open"] == -1
    assert over.json()["personDays"] == 1


async def test_the_other_rules(api: Api) -> None:
    w = await world(api)
    not_monday = await assign(w.admin, w.task["id"], w.backend["id"], w.ada["id"], "2030-01-09")
    assert not_monday.status_code == 422
    assert not_monday.json()["code"] == "WEEK_NOT_MONDAY"

    missing_member = await assign(w.admin, w.task["id"], w.backend["id"], str(uuid.uuid4()))
    assert missing_member.status_code == 422
    assert missing_member.json()["errors"][0] == {
        "field": "memberId",
        "message": "The member does not exist.",
        "code": "not_found",
    }
    missing_task = await assign(w.admin, str(uuid.uuid4()), w.backend["id"], w.ada["id"])
    assert missing_task.json()["errors"][0]["field"] == "taskId"

    for days in (0, -1):
        assert (
            await assign(w.admin, w.task["id"], w.backend["id"], w.ada["id"], days=days)
        ).status_code == 422

    sleeper = await member(w.admin, w.team["id"], "Cy", (w.backend["id"],), active=False)
    inactive = await assign(w.admin, w.task["id"], w.backend["id"], sleeper["id"])
    assert inactive.status_code == 422
    assert inactive.json()["code"] == "MEMBER_INACTIVE"


async def test_a_closed_task_takes_no_new_work_and_keeps_its_history(api: Api) -> None:
    w = await world(api)
    kept = await booked(w.admin, w.task["id"], w.backend["id"], w.ada["id"], week(), 1)
    done = await w.admin.put(
        f"{TASKS}/{w.task['id']}",
        {"key": "T-1", "title": "x", "status": "done"},
        headers={"If-Match": '"1"'},
    )
    assert done.status_code == 200

    create = await assign(w.admin, w.task["id"], w.backend["id"], w.ada["id"], week(1))
    assert create.status_code == 422 and create.json()["code"] == "TASK_CLOSED"
    update = await w.admin.put(
        f"{ASSIGNMENTS}/{kept['id']}",
        {"memberId": w.ada["id"], "weekStart": week(), "personDays": 2},
        headers={"If-Match": '"1"'},
    )
    assert update.status_code == 422 and update.json()["code"] == "TASK_CLOSED"
    delete = await w.admin.delete(f"{ASSIGNMENTS}/{kept['id']}", params={"version": 1})
    assert delete.status_code == 422 and delete.json()["code"] == "TASK_CLOSED"
    shown = (await w.admin.get(f"{ASSIGNMENTS}/{kept['id']}")).json()
    assert shown["closed"] is True and shown["writable"] is False


async def test_the_same_task_skill_member_and_week_is_merged(api: Api) -> None:
    w = await world(api)
    first = await assign(w.admin, w.task["id"], w.backend["id"], w.ada["id"], week(), 1)
    second = await assign(w.admin, w.task["id"], w.backend["id"], w.ada["id"], week(), 1.5)
    assert (first.status_code, second.status_code) == (201, 200)
    merged = second.json()
    assert merged["id"] == first.json()["id"]
    assert merged["mergedInto"] == first.json()["id"]
    assert merged["personDays"] == 2.5
    assert merged["version"] == 2
    assert (await w.admin.get(ASSIGNMENTS)).json()["total"] == 1


async def test_moving_an_assignment_onto_another_merges_them(api: Api) -> None:
    w = await world(api)
    here = await booked(w.admin, w.task["id"], w.backend["id"], w.ada["id"], week(0), 1)
    there = await booked(w.admin, w.task["id"], w.backend["id"], w.ada["id"], week(1), 2)
    moved = await w.admin.put(
        f"{ASSIGNMENTS}/{here['id']}",
        {"memberId": w.ada["id"], "weekStart": week(1), "personDays": 1},
        headers={"If-Match": '"1"'},
    )
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert body["id"] == there["id"]
    assert body["mergedInto"] == there["id"]
    assert body["deletedId"] == here["id"]
    assert body["personDays"] == 3
    assert [w_["weekStart"] for w_ in body["memberWeeks"]] == [week(1), week(0)]
    assert (await w.admin.get(f"{ASSIGNMENTS}/{here['id']}")).status_code == 404
    assert (await w.admin.get(ASSIGNMENTS)).json()["total"] == 1


async def test_an_assignment_moves_to_another_member_and_week(api: Api) -> None:
    w = await world(api)
    created = await booked(w.admin, w.task["id"], w.backend["id"], w.ada["id"], week(0), 2)
    moved = await w.admin.put(
        f"{ASSIGNMENTS}/{created['id']}",
        {"memberId": w.bob["id"], "weekStart": week(2), "personDays": 1.5},
        headers={"If-Match": '"1"'},
    )
    assert moved.status_code == 200
    body = moved.json()
    assert (body["id"], body["memberName"], body["weekStart"], body["personDays"]) == (
        created["id"],
        "Bob",
        week(2),
        1.5,
    )
    assert body["version"] == 2
    assert body["deletedId"] is None and body["mergedInto"] is None
    assert len(body["memberWeeks"]) == 2

    # DM-02 holds for a move: the target must have the skill
    qa_row = await booked(w.admin, w.task["id"], w.qa["id"], w.bob["id"], week(0), 1)
    refused = await w.admin.put(
        f"{ASSIGNMENTS}/{qa_row['id']}",
        {"memberId": w.ada["id"], "weekStart": week(0), "personDays": 1},
        headers={"If-Match": '"1"'},
    )
    assert refused.status_code == 422
    assert refused.json()["code"] == "ASSIGNMENT_SKILL_MISMATCH"


async def test_dm02_work_that_lost_its_skill_stays_and_can_be_reduced(api: Api) -> None:
    w = await world(api)
    kept = await booked(w.admin, w.task["id"], w.backend["id"], w.ada["id"], week(), 3)
    current = (await w.admin.get(f"{MEMBERS}/{w.ada['id']}")).json()
    stripped = await w.admin.put(
        f"{MEMBERS}/{w.ada['id']}/skills", {"skillIds": [], "version": current["version"]}
    )
    assert stripped.status_code == 200
    assert stripped.json()["conflicts"][0]["taskKey"] == "T-1"

    reduced = await w.admin.put(
        f"{ASSIGNMENTS}/{kept['id']}",
        {"memberId": w.ada["id"], "weekStart": week(), "personDays": 1},
        headers={"If-Match": '"1"'},
    )
    assert reduced.status_code == 200, "the member is unchanged, so only the amount is checked"
    assert reduced.json()["personDays"] == 1
    new_work = await assign(w.admin, w.task["id"], w.backend["id"], w.ada["id"], week(1))
    assert new_work.json()["code"] == "ASSIGNMENT_SKILL_MISMATCH"


async def test_deleting_an_assignment_reports_the_new_state(api: Api) -> None:
    w = await world(api)
    created = await booked(w.admin, w.task["id"], w.backend["id"], w.ada["id"], week(), 5)
    assert created["overbooked"] is True
    deleted = await w.admin.delete(f"{ASSIGNMENTS}/{created['id']}", params={"version": 1})
    assert deleted.status_code == 200
    body = deleted.json()
    assert body["deletedId"] == created["id"]
    assert body["overbooked"] is False
    assert body["memberWeeks"][0]["booked"] == 0
    assert body["taskSkill"]["assigned"] == 0
    assert (await w.admin.get(f"{ASSIGNMENTS}/{created['id']}")).status_code == 404


async def test_acceptance_12_the_second_of_two_updates_is_refused(api: Api) -> None:
    w = await world(api)
    created = await booked(w.admin, w.task["id"], w.backend["id"], w.ada["id"], week(), 1)
    other = await api.as_role("planner")
    change = {"memberId": w.ada["id"], "weekStart": week(), "personDays": 2}
    first = await w.admin.put(f"{ASSIGNMENTS}/{created['id']}", change, headers={"If-Match": '"1"'})
    assert first.status_code == 200
    second = await other.put(
        f"{ASSIGNMENTS}/{created['id']}",
        {**change, "personDays": 3},
        headers={"If-Match": '"1"'},
    )
    assert second.status_code == 412
    assert second.json()["code"] == "VERSION_MISMATCH"
    assert second.json()["currentVersion"] == 2
    by_body = await other.put(f"{ASSIGNMENTS}/{created['id']}", {**change, "version": 1})
    assert by_body.status_code == 409
    assert (await w.admin.get(f"{ASSIGNMENTS}/{created['id']}")).json()["personDays"] == 2
    gone = await other.delete(f"{ASSIGNMENTS}/{created['id']}", params={"version": 1})
    assert gone.status_code == 409


async def test_acceptance_13_a_dry_run_changes_nothing(api: Api) -> None:
    w = await world(api)
    await booked(w.admin, w.task["id"], w.backend["id"], w.ada["id"], week(), 4)
    entries_before = await audit_count(w.admin)

    probe = await assign(
        w.admin, w.task["id"], w.backend["id"], w.bob["id"], week(), 1, dryRun="true"
    )
    assert probe.status_code == 201
    assert probe.json()["dryRun"] is True
    assert "etag" not in probe.headers and "location" not in probe.headers
    overbooked = await assign(
        w.admin, w.task["id"], w.backend["id"], w.ada["id"], week(), 1, dryRun="true"
    )
    assert overbooked.json()["dryRun"] is True
    assert overbooked.json()["overbooked"] is True
    assert overbooked.json()["mergedInto"] is not None

    listing = (await w.admin.get(ASSIGNMENTS)).json()
    assert listing["total"] == 1
    assert listing["items"][0]["personDays"] == 4, "the merge was rolled back"
    assert await audit_count(w.admin) == entries_before, "a dry run writes no audit entry"

    row = listing["items"][0]
    moved = await w.admin.put(
        f"{ASSIGNMENTS}/{row['id']}",
        {"memberId": w.bob["id"], "weekStart": week(1), "personDays": 1},
        headers={"If-Match": '"1"'},
        params={"dryRun": "true"},
    )
    assert moved.status_code == 200 and moved.json()["dryRun"] is True
    deleted = await w.admin.delete(
        f"{ASSIGNMENTS}/{row['id']}", params={"version": 1, "dryRun": "true"}
    )
    assert deleted.status_code == 200 and deleted.json()["dryRun"] is True
    after = (await w.admin.get(f"{ASSIGNMENTS}/{row['id']}")).json()
    assert (after["memberId"], after["weekStart"], after["version"]) == (
        w.ada["id"],
        week(),
        1,
    )


async def test_a_dry_run_still_checks_the_rules_and_the_permission(api: Api) -> None:
    w = await world(api)
    refused = await assign(w.admin, w.task["id"], w.qa["id"], w.ada["id"], dryRun="true")
    assert refused.status_code == 422
    viewer = await api.as_role("viewer")
    nope = await assign(viewer, w.task["id"], w.backend["id"], w.ada["id"], dryRun="true")
    assert nope.status_code == 403


async def test_acceptance_8_a_viewer_cannot_assign(api: Api) -> None:
    w = await world(api)
    viewer = await api.as_role("viewer")
    response = await assign(viewer, w.task["id"], w.backend["id"], w.ada["id"])
    assert response.status_code == 403
    assert response.json()["permission"] == "assignment:write"
    assert (await viewer.get(ASSIGNMENTS)).status_code == 200


async def test_a_team_lead_plans_only_the_teams_of_the_role(api: Api) -> None:
    w = await world(api)
    gamma = await team(w.admin, "Gamma")
    gus = await member(w.admin, gamma["id"], "Gus", (w.backend["id"],))
    lead = await api.as_role("team-lead", teams=[uuid.UUID(w.team["id"])])

    own = await assign(lead, w.task["id"], w.backend["id"], w.ada["id"])
    assert own.status_code == 201
    assert own.json()["writable"] is True
    foreign = await assign(lead, w.task["id"], w.backend["id"], gus["id"])
    assert foreign.status_code == 403

    row = own.json()
    move = await lead.put(
        f"{ASSIGNMENTS}/{row['id']}",
        {"memberId": gus["id"], "weekStart": week(), "personDays": 1},
        headers={"If-Match": '"1"'},
    )
    assert move.status_code == 403, "the target team counts as well"

    elsewhere = await booked(w.admin, w.task["id"], w.backend["id"], gus["id"], week(), 1)
    listed = {a["id"]: a for a in (await lead.get(ASSIGNMENTS)).json()["items"]}
    assert listed[elsewhere["id"]]["writable"] is False
    assert listed[row["id"]]["writable"] is True
    assert (
        await lead.delete(f"{ASSIGNMENTS}/{elsewhere['id']}", params={"version": 1})
    ).status_code == 403


async def test_acceptance_7_a_cross_team_task_takes_members_of_every_team(api: Api) -> None:
    admin = await api.admin()
    skills = [await skill(admin, k) for k in ("Backend", "Frontend", "QA")]
    teams = [await team(admin, n) for n in ("Alpha", "Beta", "Gamma")]
    people = [
        await member(admin, t["id"], f"P{i}", (s["id"],))
        for i, (t, s) in enumerate(zip(teams, skills, strict=True))
    ]
    shared = await task(admin, "X-1", {s["id"]: "2" for s in skills}, ownerTeamId=teams[1]["id"])
    for person, wanted in zip(people, skills, strict=True):
        response = await assign(admin, shared["id"], wanted["id"], person["id"])
        assert response.status_code == 201, response.text
    assert (await admin.get(TASKS + f"/{shared['id']}")).json()["totalAssigned"] == 3


async def test_the_list_filters(api: Api) -> None:
    w = await world(api)
    await booked(w.admin, w.task["id"], w.backend["id"], w.ada["id"], week(0), 1)
    await booked(w.admin, w.task["id"], w.backend["id"], w.bob["id"], week(1), 1)
    await booked(w.admin, w.task["id"], w.qa["id"], w.bob["id"], week(2), 1)

    async def names(**params: Any) -> list[str]:
        items = (await w.admin.get(ASSIGNMENTS, params=params)).json()["items"]
        return [f"{a['memberName']}:{a['skillKey']}:{a['weekStart']}" for a in items]

    assert len(await names()) == 3
    assert await names(memberId=w.ada["id"]) == [f"Ada:Backend:{week(0)}"]
    assert await names(skillId=w.qa["id"]) == [f"Bob:QA:{week(2)}"]
    assert await names(**{"from": week(1), "to": week(1)}) == [f"Bob:Backend:{week(1)}"]
    assert len(await names(taskId=w.task["id"])) == 3
    assert len(await names(teamId=w.team["id"])) == 3
    assert await names(sort="-weekStart", pageSize=1) == [f"Bob:QA:{week(2)}"]


async def test_changes_are_audited_with_the_client_and_the_way_in(api: Api) -> None:
    w = await world(api)
    created = await booked(w.admin, w.task["id"], w.backend["id"], w.ada["id"], week(), 1)
    await assign(w.admin, w.task["id"], w.backend["id"], w.ada["id"], week(), 1)
    entries = (await w.admin.get(AUDIT, params={"entityType": "assignment"})).json()["items"]
    assert [e["action"] for e in entries] == ["merge", "create"]
    assert all(e["entityId"] == created["id"] for e in entries)
    assert entries[0]["before"]["person_days"] == 1 and entries[0]["after"]["person_days"] == 2
    assert entries[0]["via"] == "session" and entries[0]["clientName"] == "tests"
