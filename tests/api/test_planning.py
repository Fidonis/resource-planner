"""Planning board, overview and availability (acceptance 1 to 4, 6; §4 rules 1 to 8)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from tests.api.support import Api
from tests.api.world import (
    ABSENCES,
    AVAILABILITY,
    OVERVIEW,
    PLANNING,
    TASKS,
    TYPES,
    booked,
    cell,
    day,
    member,
    plan,
    skill,
    task,
    team,
    week,
)

from planner_api import clock


async def person(
    admin: Any, team_id: object, name: str, skills: tuple[object, ...] = (), **extra: Any
) -> dict[str, Any]:
    """A member at full allocation, unless the test says otherwise (the server default is 80 %)."""
    return await member(admin, team_id, name, skills, **{"allocationPercent": 100, **extra})


async def away(
    admin: Any, who: str | None, key: str, start: str, end: str | None = None, **p: Any
) -> None:
    body = {
        "memberId": who,
        "absenceTypeId": await _type(admin, key),
        "startDate": start,
        "endDate": end or start,
        **p,
    }
    response = await admin.post(ABSENCES, body)
    assert response.status_code == 201, response.text


async def _type(admin: Any, key: str) -> str:
    listing = (await admin.get(TYPES)).json()["items"]
    return str(next(t["id"] for t in listing if t["key"] == key))


async def test_a_week_of_vacation_leaves_no_capacity(api: Api) -> None:
    """Acceptance 1."""
    admin = await api.admin()
    ada = await person(admin, (await team(admin))["id"], "Ada")
    await away(admin, ada["id"], "vacation", day(0), day(4))
    figures = cell(await plan(admin), ada["id"])
    assert (figures["presentDays"], figures["absentDays"], figures["capacity"]) == (0, 5, 0)
    assert figures["free"] == 0 and figures["overbooked"] is False and figures["full"] is False


async def test_a_half_day_off_costs_half_a_person_day(api: Api) -> None:
    """Acceptance 2: 100 % allocation, morning off on one day gives 4.5."""
    admin = await api.admin()
    ada = await person(admin, (await team(admin))["id"], "Ada")
    await away(admin, ada["id"], "vacation", day(1), startPart="AM", endPart="AM")
    figures = cell(await plan(admin), ada["id"])
    assert (figures["absentDays"], figures["capacity"]) == (0.5, 4.5)


async def test_allocation_scales_the_capacity(api: Api) -> None:
    admin = await api.admin()
    ada = await person(admin, (await team(admin))["id"], "Ada", allocationPercent=60)
    await away(admin, ada["id"], "sick", day(0))
    figures = cell(await plan(admin), ada["id"])
    assert (figures["presentDays"], figures["capacity"]) == (4, 2.4)


async def test_a_company_holiday_applies_to_everybody(api: Api) -> None:
    """Acceptance 3: a Thursday holiday for all members."""
    admin = await api.admin()
    alpha, beta = await team(admin, "Alpha"), await team(admin, "Beta")
    ada = await person(admin, alpha["id"], "Ada")
    bob = await person(admin, beta["id"], "Bob", allocationPercent=50)
    await away(admin, None, "public-holiday", day(3))
    planning = await plan(admin)
    assert cell(planning, ada["id"])["capacity"] == 4
    assert cell(planning, bob["id"])["capacity"] == 2
    # the same morning twice costs it once
    await away(admin, ada["id"], "vacation", day(3), startPart="AM", endPart="AM")
    assert cell(await plan(admin), ada["id"])["capacity"] == 4


async def test_an_absence_that_costs_no_capacity_changes_no_figure(api: Api) -> None:
    """Acceptance 4."""
    admin = await api.admin()
    ada = await person(admin, (await team(admin))["id"], "Ada")
    info = await admin.create(
        TYPES,
        {
            "key": "on-call",
            "name": "On-call",
            "color": "#5b6b7a",
            "letter": "O",
            "reducesCapacity": False,
        },
    )
    await admin.create(
        ABSENCES,
        {
            "memberId": ada["id"],
            "absenceTypeId": info["id"],
            "startDate": day(0),
            "endDate": day(4),
        },
    )
    figures = cell(await plan(admin), ada["id"])
    assert (figures["absentDays"], figures["capacity"]) == (0, 5)
    calendar = (await admin.get(ABSENCES + "/calendar", params={"from": week(), "weeks": 1})).json()
    assert calendar["members"][0]["days"][0]["am"]["letter"] == "O"


async def test_booking_more_than_the_capacity_is_flagged_not_blocked(api: Api) -> None:
    """Acceptance 6."""
    admin = await api.admin()
    backend = await skill(admin)
    ada = await person(admin, (await team(admin))["id"], "Ada", (backend["id"],))
    work = await task(admin, "T-1", {backend["id"]: "8"})
    await booked(admin, work["id"], backend["id"], ada["id"], week(), 4)
    full = cell(await plan(admin), ada["id"])
    assert (full["booked"], full["free"], full["full"], full["overbooked"]) == (4, 1, False, False)
    await booked(admin, work["id"], backend["id"], ada["id"], week(1), 5)
    exactly = cell(await plan(admin, 2), ada["id"], 1)
    assert exactly["full"] is True and exactly["overbooked"] is False
    over = await admin.post(
        "/api/v1/assignments",
        {
            "taskId": work["id"],
            "skillId": backend["id"],
            "memberId": ada["id"],
            "weekStart": week(),
            "personDays": 2,
        },
    )
    assert over.status_code in (200, 201) and over.json()["overbooked"] is True
    flagged = cell(await plan(admin), ada["id"])
    assert (flagged["booked"], flagged["free"], flagged["overbooked"]) == (6, -1, True)


async def test_the_board_lists_teams_members_chips_and_the_task_pool(api: Api) -> None:
    admin = await api.admin()
    backend, qa = await skill(admin, "Backend"), await skill(admin, "QA")
    alpha = await team(admin, "Alpha")
    ada = await person(admin, alpha["id"], "Ada", (backend["id"],))
    work = await task(admin, "T-1", {backend["id"]: "4", qa["id"]: "2"})
    chip = await booked(admin, work["id"], backend["id"], ada["id"], week(), 3)

    planning = await plan(admin, 2)
    assert planning["start"] == week() and len(planning["weeks"]) == 2
    assert planning["canAssign"] is True
    assert {s["key"] for s in planning["skills"]} == {"Backend", "QA"}
    [team_plan] = planning["teams"]
    assert (team_plan["name"], team_plan["capacity"], team_plan["booked"]) == ("Alpha", 10, 3)
    [row] = team_plan["members"]
    assert row["writable"] is True and row["skillIds"] == [backend["id"]]
    assert [c["id"] for c in row["weeks"][0]["assignments"]] == [chip["id"]]
    assert row["weeks"][1]["assignments"] == []
    assert row["weeks"][0]["assignments"][0]["taskKey"] == "T-1"

    [pooled] = planning["tasks"]
    assert (pooled["key"], pooled["totalEffort"], pooled["totalOpen"]) == ("T-1", 6, 3)
    assert pooled["plannedPercent"] == 50.0
    efforts = {e["skillKey"]: e for e in pooled["efforts"]}
    assert (efforts["Backend"]["assigned"], efforts["Backend"]["open"]) == (3, 1)
    assert efforts["Backend"]["done"] is False and efforts["QA"]["open"] == 2


async def test_a_viewer_sees_the_board_but_cannot_assign(api: Api) -> None:
    admin = await api.admin()
    backend = await skill(admin)
    alpha = await team(admin)
    await person(admin, alpha["id"], "Ada", (backend["id"],))
    await task(admin, "T-1", {backend["id"]: "1"})
    viewer = await api.as_role("viewer")
    planning = await plan(viewer)
    assert planning["canAssign"] is False
    assert planning["teams"][0]["members"][0]["writable"] is False
    lead = await api.as_role("team-lead", teams=[uuid.UUID(alpha["id"])])
    other = await team(admin, "Beta")
    await person(admin, other["id"], "Bob", (backend["id"],))
    seen = {
        m["displayName"]: m["writable"] for t in (await plan(lead))["teams"] for m in t["members"]
    }
    assert seen == {"Ada": True, "Bob": False}


async def test_slices_limit_the_board_to_what_a_change_touched(api: Api) -> None:
    admin = await api.admin()
    backend = await skill(admin)
    alpha, beta = await team(admin, "Alpha"), await team(admin, "Beta")
    ada = await person(admin, alpha["id"], "Ada", (backend["id"],))
    bob = await person(admin, beta["id"], "Bob", (backend["id"],))
    one = await task(admin, "T-1", {backend["id"]: "2"})
    two = await task(admin, "T-2", {backend["id"]: "2"})
    await booked(admin, one["id"], backend["id"], ada["id"], week(), 1)

    only_ada = await plan(admin, memberId=ada["id"], withTasks="false")
    assert [t["name"] for t in only_ada["teams"]] == ["Alpha"]
    assert [m["id"] for m in only_ada["teams"][0]["members"]] == [ada["id"]]
    assert only_ada["tasks"] == []

    both = await plan(admin, memberId=[ada["id"], bob["id"]])
    assert [t["name"] for t in both["teams"]] == ["Alpha", "Beta"]

    only_tasks = await plan(admin, withMembers="false")
    assert only_tasks["teams"] == [] and {t["key"] for t in only_tasks["tasks"]} == {"T-1", "T-2"}

    one_task = await plan(admin, withMembers="false", taskId=two["id"])
    assert [t["key"] for t in one_task["tasks"]] == ["T-2"]

    in_beta = await plan(admin, teamId=beta["id"])
    assert [t["name"] for t in in_beta["teams"]] == ["Beta"]


async def test_the_pool_hides_planned_work_and_can_be_limited_to_a_skill(api: Api) -> None:
    admin = await api.admin()
    backend, qa = await skill(admin, "Backend"), await skill(admin, "QA")
    ada = await person(admin, (await team(admin))["id"], "Ada", (backend["id"],))
    finished = await task(admin, "T-1", {backend["id"]: "1"})
    mixed = await task(admin, "T-2", {backend["id"]: "1", qa["id"]: "2"})
    await booked(admin, finished["id"], backend["id"], ada["id"], week(), 1)
    await booked(admin, mixed["id"], backend["id"], ada["id"], week(), 1)

    assert {t["key"] for t in (await plan(admin))["tasks"]} == {"T-2"}
    assert {t["key"] for t in (await plan(admin, onlyOpenEffort="false"))["tasks"]} == {
        "T-1",
        "T-2",
    }
    by_backend = (await plan(admin, skillId=backend["id"]))["tasks"]
    assert by_backend == [], "Backend is fully planned, only QA is open"
    by_qa = (await plan(admin, skillId=qa["id"]))["tasks"]
    assert [(t["key"], [e["skillKey"] for e in t["efforts"]]) for t in by_qa] == [("T-2", ["QA"])]


async def test_closed_tasks_leave_the_pool_and_stop_booking_capacity(api: Api) -> None:
    admin = await api.admin()
    backend = await skill(admin)
    ada = await person(admin, (await team(admin))["id"], "Ada", (backend["id"],))
    work = await task(admin, "T-1", {backend["id"]: "4"})
    await booked(admin, work["id"], backend["id"], ada["id"], week(), 3)
    assert cell(await plan(admin), ada["id"])["booked"] == 3

    async def set_status(status: str, version: int) -> None:
        response = await admin.put(
            f"{TASKS}/{work['id']}",
            {"key": "T-1", "title": "x", "status": status},
            headers={"If-Match": f'"{version}"'},
        )
        assert response.status_code == 200, response.text

    await set_status("cancelled", 1)
    after = await plan(admin)
    assert cell(after, ada["id"])["booked"] == 0, "a cancelled task frees the capacity"
    assert after["tasks"] == []
    await set_status("done", 2)
    done = await plan(admin)
    assert cell(done, ada["id"])["booked"] == 3, "done work stays booked"
    assert done["tasks"] == []
    only = await plan(admin, withMembers="false", taskId=work["id"])
    assert [(t["key"], t["status"]) for t in only["tasks"]] == [("T-1", "done")]
    chip = cell(done, ada["id"])["assignments"][0]
    assert chip["closed"] is True


async def test_an_inactive_member_stays_visible_only_while_holding_work(api: Api) -> None:
    admin = await api.admin()
    backend = await skill(admin)
    alpha = await team(admin)
    gone = await person(admin, alpha["id"], "Gone", (backend["id"],))
    await person(admin, alpha["id"], "Idle", (backend["id"],), active=False)
    work = await task(admin, "T-1", {backend["id"]: "1"})
    await booked(admin, work["id"], backend["id"], gone["id"], week(), 1)
    changed = await admin.put(
        f"/api/v1/members/{gone['id']}",
        {
            **{k: gone[k] for k in ("displayName", "teamId", "allocationPercent")},
            "skillIds": [backend["id"]],
            "active": False,
            "version": gone["version"],
        },
    )
    assert changed.status_code == 200, changed.text
    names = [m["displayName"] for t in (await plan(admin))["teams"] for m in t["members"]]
    assert names == ["Gone"]
    later = [
        m["displayName"] for t in (await plan(admin, 1, week(5)))["teams"] for m in t["members"]
    ]
    assert later == []


async def test_the_horizon_defaults_to_this_week_and_the_configured_length(api: Api) -> None:
    admin = await api.admin()
    await person(admin, (await team(admin))["id"], "Ada")
    with clock.pinned(date(2030, 1, 9)):  # a Wednesday
        default = (await admin.get(PLANNING)).json()
        longest = (await admin.get(PLANNING, params={"weeks": 26})).json()
        too_long = await admin.get(PLANNING, params={"weeks": 27})
        mid = (await admin.get(PLANNING, params={"from": day(10), "weeks": 1})).json()
    assert default["start"] == week()
    assert len(default["weeks"]) == 4
    assert len(longest["weeks"]) == 26 and too_long.status_code == 422
    assert mid["start"] == week(1), "any day of a week selects that week's Monday"
    [first] = mid["weeks"]
    assert (first["isoWeek"], first["isoYear"]) == (3, 2030)


async def test_the_overview_counts_demand_assigned_and_the_pool(api: Api) -> None:
    """Acceptance 7: Backend has capacity 5, demand 4.5 is tight, 3 is fine, 6 is a bottleneck."""
    admin = await api.admin()
    backend = await skill(admin, "Backend")
    alpha = await team(admin, "Alpha")
    ada = await person(admin, alpha["id"], "Ada", (backend["id"],))
    await task(admin, "OK", {backend["id"]: "3"})
    overview = (await admin.get(OVERVIEW, params={"from": week(), "weeks": 1})).json()
    [row] = overview["skills"]
    assert (row["demand"], row["assigned"], row["open"]) == (3, 0, 3)
    assert (row["pool"], row["poolFree"], row["status"]) == (5, 5, "OK")
    assert overview["kpis"] == {
        "demand": 3,
        "assigned": 0,
        "assignedPercent": 0.0,
        "open": 3,
        "overbookedMemberWeeks": 0,
    }

    await task(admin, "MORE", {backend["id"]: "1.5"})
    tight = (await admin.get(OVERVIEW, params={"from": week(), "weeks": 1})).json()
    assert tight["skills"][0]["open"] == 4.5 and tight["skills"][0]["status"] == "TIGHT"

    await task(admin, "MOST", {backend["id"]: "1.5"})
    short = (await admin.get(OVERVIEW, params={"from": week(), "weeks": 1})).json()
    assert short["skills"][0]["open"] == 6 and short["skills"][0]["status"] == "BOTTLENECK"
    assert ada["id"]


async def test_the_overview_counts_the_whole_assignment_not_only_the_horizon(api: Api) -> None:
    admin = await api.admin()
    backend = await skill(admin)
    alpha = await team(admin, "Alpha")
    ada = await person(admin, alpha["id"], "Ada", (backend["id"],))
    work = await task(admin, "T-1", {backend["id"]: "4"})
    await booked(admin, work["id"], backend["id"], ada["id"], week(0), 1)
    await booked(admin, work["id"], backend["id"], ada["id"], week(8), 2)
    overview = (await admin.get(OVERVIEW, params={"from": week(), "weeks": 2})).json()
    [row] = overview["skills"]
    assert (row["demand"], row["assigned"], row["open"]) == (4, 3, 1)
    assert overview["kpis"]["assignedPercent"] == 75.0
    [alpha_row] = overview["teams"]
    assert (alpha_row["capacity"], alpha_row["booked"], alpha_row["free"]) == (10, 1, 9)
    assert alpha_row["utilisationPercent"] == 10.0


async def test_over_planning_is_reported_and_does_not_make_the_overview_negative(api: Api) -> None:
    admin = await api.admin()
    backend = await skill(admin)
    ada = await person(admin, (await team(admin))["id"], "Ada", (backend["id"],))
    work = await task(admin, "T-1", {backend["id"]: "1"})
    await booked(admin, work["id"], backend["id"], ada["id"], week(), 3)
    [row] = (await admin.get(OVERVIEW, params={"from": week(), "weeks": 1})).json()["skills"]
    assert (row["assigned"], row["open"], row["status"]) == (1, 0, "OK")
    pooled = (await plan(admin, onlyOpenEffort="false"))["tasks"][0]["efforts"][0]
    assert (pooled["assigned"], pooled["open"], pooled["overplanned"]) == (3, -2, True)


async def test_the_overview_exports_both_tables_as_csv(api: Api) -> None:
    admin = await api.admin()
    backend = await skill(admin)
    await person(admin, (await team(admin))["id"], "Ada", (backend["id"],))
    await task(admin, "T-1", {backend["id"]: "2"})
    for table, header in (("skills", "Skill"), ("teams", "Team")):
        response = await admin.get(
            OVERVIEW + "/export-csv", params={"table": table, "from": week(), "weeks": 1}
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/csv")
        assert header in response.text.splitlines()[0]
        assert "attachment" in response.headers["content-disposition"]


async def test_availability_ranks_members_with_the_skill_by_free_days(api: Api) -> None:
    admin = await api.admin()
    backend, qa = await skill(admin, "Backend"), await skill(admin, "QA")
    alpha = await team(admin, "Alpha")
    ada = await person(admin, alpha["id"], "Ada", (backend["id"],))
    bob = await person(admin, alpha["id"], "Bob", (backend["id"], qa["id"]))
    cid = await person(admin, alpha["id"], "Cid", (backend["id"],), allocationPercent=50)
    await person(admin, alpha["id"], "Dan", (qa["id"],))
    await person(admin, alpha["id"], "Eve", (backend["id"],), active=False)
    work = await task(admin, "T-1", {backend["id"]: "9"})
    await booked(admin, work["id"], backend["id"], ada["id"], week(), 4)
    await away(admin, bob["id"], "vacation", day(0), day(1))

    response = await admin.get(
        AVAILABILITY, params={"skillId": backend["id"], "from": week(), "weeks": 2}
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert (data["skillKey"], data["start"], len(data["weeks"])) == ("Backend", week(), 2)
    assert [(m["displayName"], m["totalFree"]) for m in data["members"]] == [
        ("Bob", 8),
        ("Ada", 6),
        ("Cid", 5),
    ]
    first = data["members"][0]
    assert first["teamName"] == "Alpha"
    assert (first["weeks"][0]["absentDays"], first["weeks"][0]["capacity"]) == (2, 3)
    assert cid["id"] in {m["memberId"] for m in data["members"]}

    scoped = await admin.get(
        AVAILABILITY, params={"skillId": backend["id"], "teamId": str(uuid.uuid4())}
    )
    assert scoped.json()["members"] == []
    unknown = await admin.get(AVAILABILITY, params={"skillId": str(uuid.uuid4())})
    assert unknown.status_code == 404
    missing = await admin.get(AVAILABILITY)
    assert missing.status_code == 422


async def test_reading_the_plan_needs_the_assignment_read_permission(api: Api) -> None:
    nobody = await api.as_role(permissions=["team:read"])
    for url in (PLANNING, OVERVIEW):
        response = await nobody.get(url)
        assert response.status_code == 403, url
        assert response.json()["permission"] == "assignment:read"
    assert (await api.anonymous().get(PLANNING)).status_code == 401
